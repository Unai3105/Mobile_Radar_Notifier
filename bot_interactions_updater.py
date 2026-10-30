from pymongo import MongoClient
import datetime
import requests
import logging
import os

# Configuración básica de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configura el token de tu bot
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GET_UPDATES_URL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates'

# Configura la conexión con MongoDB Atlas
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)

# Selecciona la base de datos y colección
MONGO_DB = os.getenv("MONGO_DB")
db = client[MONGO_DB]
MONGO_COLLECTION_INTERACTIONS = os.getenv("MONGO_COLLECTION_INTERACTIONS")
collection = db[MONGO_COLLECTION_INTERACTIONS]

# Colección auxiliar para guardar el último update_id procesado de Telegram
collection_state = db["bot_state"]
STATE_ID = "telegram_updates"

def obtener_ultimo_update_id():
    estado = collection_state.find_one({"_id": STATE_ID})
    return estado.get("last_update_id") if estado else None

def guardar_ultimo_update_id(update_id):
    collection_state.update_one(
        {"_id": STATE_ID},
        {"$set": {"last_update_id": update_id}},
        upsert=True
    )

def obtener_interacciones():
    try:
        logging.info("Iniciando la solicitud a la API de Telegram...")
        ultimo_update_id = obtener_ultimo_update_id()
        params = {"offset": ultimo_update_id + 1} if ultimo_update_id is not None else {}
        response = requests.get(GET_UPDATES_URL, params=params, timeout=30)
        data = response.json()

        if data.get('ok'):
            logging.info(f"Se han obtenido {len(data['result'])} interacciones.")
            return data['result']
        else:
            logging.error("Error al obtener las interacciones de Telegram: respuesta no válida.")
            raise ValueError("Respuesta no válida de la API de Telegram.")
    
    except Exception as e:
        logging.error(f"Error en la solicitud HTTP: {e}")
        raise  # Vuelve a lanzar la excepción para propagar el error

def transformar_a_estructura_mongo(interacciones):
    try:
        chats = []
        logging.info("Transformando las interacciones a la estructura para MongoDB...")

        for interaccion in interacciones:
            mensaje = interaccion.get('message', {})

            if mensaje:
                chat_id = mensaje['chat']['id']
                first_name = mensaje['chat'].get('first_name', '')
                username = mensaje['chat'].get('username', '')
                chat_type = mensaje['chat']['type']

                # Verificamos si este chat ya existe en la lista de chats
                chat = next((c for c in chats if c['chat_id'] == chat_id), None)

                if not chat:
                    # Si no existe, lo creamos
                    chat = {
                        "chat_id": chat_id,
                        "first_name": first_name,
                        "username": username,
                        "chat_type": chat_type,
                        "active": True,  # Inicialmente está activo
                        "messages": []
                    }
                    chats.append(chat)

                # Ahora agregamos el mensaje a este chat
                chat['messages'].append({
                    "message_id": mensaje['message_id'],
                    "date": mensaje['date'],
                    "text": mensaje.get('text', ''),
                    "command": '',
                    "entities": []
                })

                # Verificamos si el mensaje tiene entidades (comandos)
                if 'entities' in mensaje:
                    for entity in mensaje['entities']:
                        if entity['type'] == 'bot_command':
                            chat['messages'][-1]['command'] = 'bot_command'
                            chat['messages'][-1]['entities'].append({
                                "offset": entity['offset'],
                                "length": entity['length'],
                                "type": entity['type']
                            })
        logging.info("Transformación completa.")
        return chats

    except Exception as e:
        logging.error(f"Error al transformar las interacciones: {e}")
        raise  # Propaga el error

def guardar_interacciones_en_bd(interacciones):
    logging.info("Guardando interacciones en la base de datos...")
    for interaction in interacciones:
        mensaje = interaction.get("messages", [])
        if not mensaje:
            logging.warning("Interacción sin mensajes, omitiendo.")
            continue

        for msg in mensaje:
            message_id = msg.get("message_id")
            if not message_id:
                logging.warning("Mensaje sin 'message_id', omitiendo.")
                continue

            chat_id = interaction.get("chat_id")

            # Verificar si el chat ya existe en la base de datos
            chat_document = collection.find_one({"chat_id": chat_id})

            if not chat_document:
                logging.info(f"Nuevo chat con ID {chat_id}, creando un nuevo documento.")
                collection.insert_one({
                    "chat_id": chat_id,
                    "first_name": interaction.get("first_name", ""),
                    "username": interaction.get("username", ""),
                    "chat_type": interaction.get("chat_type", ""),
                    "active": True,  # Inicialmente está activo
                    "messages": [
                        {
                            "message_id": message_id,
                            "date": datetime.datetime.fromtimestamp(msg["date"], datetime.timezone.utc),
                            "text": msg.get("text", ""),
                            "command": msg.get("command", ""),
                            "entities": msg.get("entities", [])
                        }
                    ]
                })
                logging.info(f"Chat con ID {chat_id} añadido a la base de datos.")

            else:
                # Si el usuario vuelve a escribir al bot, se considera activo.
                # (Los que bloquean el bot se marcan como inactivos al recibir
                # un 403 en telegram_radar_notifier.py)
                collection.update_one(
                    {"chat_id": chat_id},
                    {"$set": {"active": True}}
                )

                # Verificamos si el mensaje ya existe en el chat
                existing_message = collection.find_one(
                    {"chat_id": chat_id, "messages.message_id": message_id}
                )
                
                if not existing_message:
                    logging.info(f"Nuevo mensaje del chat {chat_id}, añadiéndolo a la base de datos.")
                    collection.update_one(
                        {"chat_id": chat_id},
                        {
                            "$push": {
                                "messages": {
                                    "message_id": message_id,
                                    "date": datetime.datetime.fromtimestamp(msg["date"], datetime.timezone.utc),
                                    "text": msg.get("text", ""),
                                    "command": msg.get("command", ""),
                                    "entities": msg.get("entities", [])
                                }
                            }
                        }
                    )
                    logging.info(f"Mensaje con ID {message_id} añadido al chat {chat_id}.")
                else:
                    logging.info(f"El mensaje con ID {message_id} ya existe en el chat {chat_id}, omitiendo.")

def main():
    interacciones = obtener_interacciones()

    if interacciones:
        chats_transformados = transformar_a_estructura_mongo(interacciones)
        guardar_interacciones_en_bd(chats_transformados)
        # Confirmar a Telegram los updates procesados para no repetirlos
        guardar_ultimo_update_id(max(i['update_id'] for i in interacciones))
    else:
        logging.warning("No se han obtenido nuevas interacciones de Telegram.")

if __name__ == '__main__':
    main()
