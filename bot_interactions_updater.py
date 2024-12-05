from pymongo import MongoClient
import datetime
import requests
import logging
import os

# Configuración básica de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configura el token de tu bot y la URL de la API de Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
UPDATES_URL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates'

# Configura la conexión con MongoDB Atlas
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)

# Selecciona la base de datos y colección
MONGO_DB = os.getenv("MONGO_DB")
logging.info(f"Nombre de la base de datos: {MONGO_DB}")

db = client[MONGO_DB]
MONGO_COLLECTION_INTERACTIONS = os.getenv("MONGO_COLLECTION_INTERACTIONS")
collection = db[MONGO_COLLECTION_INTERACTIONS]

def obtener_interacciones():
    try:
        logging.info("Iniciando la solicitud a la API de Telegram...")
        # Realiza la solicitud a la API de Telegram para obtener las interacciones
        response = requests.get(UPDATES_URL)
        data = response.json()

        # Verifica si la respuesta contiene resultados
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
            
            # Si el mensaje tiene un chat, lo procesamos
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
                        "messages": []
                    }
                    chats.append(chat)

                # Ahora agregamos el mensaje a este chat
                chat['messages'].append({
                    "message_id": mensaje['message_id'],
                    "date": mensaje['date'],
                    "text": mensaje['text'],
                    "command": '',
                    "entities": []
                })

                # Comprobamos si el mensaje tiene entidades (comandos)
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
    """
    Guarda las interacciones del bot en la base de datos de MongoDB, evitando duplicados.
    """
    logging.info("Guardando interacciones en la base de datos...")
    for interaction in interacciones:
        # Comprobamos si el mensaje tiene el ID y el texto
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

            # Verifica si el chat ya existe en la base de datos
            chat_document = collection.find_one({"chat_id": chat_id})
            
            # Si el chat no existe, lo creamos con el primer mensaje
            if not chat_document:
                logging.info(f"Nuevo chat con ID {chat_id}, creando un nuevo documento.")
                collection.insert_one({
                    "chat_id": chat_id,
                    "first_name": interaction.get("first_name", ""),
                    "username": interaction.get("username", ""),
                    "chat_type": interaction.get("chat_type", ""),
                    "messages": [
                        {
                            "message_id": message_id,
                            "date": datetime.datetime.utcfromtimestamp(msg["date"]),
                            "text": msg["text"],
                            "command": msg.get("command", ""),  # Agregar comando
                            "entities": msg.get("entities", [])  # Agregar entidades
                        }
                    ]
                })
                logging.info(f"Chat con ID {chat_id} añadido a la base de datos.")
            
            else:
                # Si el chat ya existe, verificamos si el mensaje ya fue guardado
                existing_message = collection.find_one(
                    {"chat_id": chat_id, "messages.message_id": message_id}
                )
                
                if not existing_message:
                    # Si el mensaje no existe, lo agregamos a la lista de mensajes
                    logging.info(f"Nuevo mensaje del chat {chat_id}, añadiéndolo a la base de datos.")
                    collection.update_one(
                        {"chat_id": chat_id},
                        {
                            "$push": {
                                "messages": {
                                    "message_id": message_id,
                                    "date": datetime.datetime.utcfromtimestamp(msg["date"]),
                                    "text": msg["text"],
                                    "command": msg.get("command", ""),  # Agregar comando
                                    "entities": msg.get("entities", [])  # Agregar entidades
                                }
                            }
                        }
                    )
                    logging.info(f"Mensaje con ID {message_id} añadido al chat {chat_id}.")
                else:
                    logging.info(f"El mensaje con ID {message_id} ya existe en el chat {chat_id}, omitiendo.")

def main():
    # Paso 1: Obtener las interacciones de la API de Telegram
    interacciones = obtener_interacciones()

    if interacciones:
        # Paso 2: Transformar las interacciones a la estructura adecuada para MongoDB
        chats_transformados = transformar_a_estructura_mongo(interacciones)

        # Paso 3: Guardar las interacciones transformadas en MongoDB
        guardar_interacciones_en_bd(chats_transformados)
    else:
        logging.warning("No se han obtenido interacciones de Telegram.")

if __name__ == '__main__':
    main()
