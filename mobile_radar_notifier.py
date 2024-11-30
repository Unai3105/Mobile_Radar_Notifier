import os
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from datetime import datetime
import logging
import traceback
import time

# Configuración básica de logueo
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Opciones avanzadas de Chrome
chrome_options = Options()
chrome_options.add_argument("--headless")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--window-size=1920x1080")

# URL de la página que quieres monitorear
url = os.getenv("DONOSTI_RADAR_WEB")

# Credenciales de Twilio desde variables de entorno
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
TO_WHATSAPP_NUMBER = os.getenv("TO_WHATSAPP_NUMBER")

# Token del bot de Telegram y el ID del chat donde se enviarán los mensajes
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")  # Token de tu bot

def inicializar_driver():
    """Inicializa y devuelve el driver de Chrome con configuraciones avanzadas."""
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        logging.info("Driver de Chrome inicializado exitosamente en modo headless.")
        return driver
    except Exception as e:
        logging.error("Error al inicializar el driver de Chrome: %s", traceback.format_exc())
        return None

def cargar_pagina(driver, url, max_retries=3):
    """Carga la página y reintenta en caso de fallos."""
    for attempt in range(max_retries):
        try:
            driver.get(url)
            logging.info(f"Página cargada correctamente en el intento {attempt + 1}.")
            return True
        except Exception as e:
            logging.warning(f"Error al cargar la página: {e}. Reintentando ({attempt + 1}/{max_retries})...")
            time.sleep(2)
    logging.error("No se pudo cargar la página después de múltiples intentos: %s", traceback.format_exc())
    return False # No lanza excepción, pero avisa de fallo.

def comprobar_radares(driver):
    """Verifica si hay radares móviles planificados para hoy y devuelve el estado como texto."""
    try:
        fecha_actual = datetime.now().strftime("%d/%m/%Y")
        elementos_span12 = driver.find_elements(By.CLASS_NAME, "span12")

        for elemento in elementos_span12:
            parrafos = elemento.find_elements(By.TAG_NAME, "p")

            for i, parrafo in enumerate(parrafos):
                texto_parrafo = parrafo.text

                if "No hay ninguna ubicación planificada para hoy." in texto_parrafo:
                    # captura_mapa(driver)
                    logging.info("No hay radares móviles planificados para hoy.")
                    return "No hay radares móviles planificados para hoy."

                elif fecha_actual in texto_parrafo and "el radar móvil estará operando en las siguientes ubicaciones" in texto_parrafo:
                    if i + 1 < len(parrafos):
                        ubicaciones = [span.text for span in parrafos[i + 1].find_elements(By.CLASS_NAME, "label")]
                        # captura_mapa(driver)
                        logging.info(f"Radares móviles encontrados: {ubicaciones}")
                        return ubicaciones

        logging.warning("Estado de radares desconocido.")
        return "Estado de radares desconocido."
    except Exception as e:
        logging.error("Error al comprobar los radares: %s", traceback.format_exc())
        raise  # Propaga el error al `main`.

def obtener_ids_usuarios():
    """Obtiene los IDs de los usuarios que han interactuado con el bot."""
    try:
        # Hacer una solicitud GET a la API de Telegram para obtener los updates
        response = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates")
        
        # Comprobar si la solicitud fue exitosa
        if response.status_code == 200:
            updates = response.json()

            # Si hay resultados, extraemos los IDs de los usuarios
            if updates.get('result'):
                ids = []
                for update in updates['result']:
                    # Verificamos que haya un mensaje y extraemos el ID del usuario
                    if 'message' in update and 'from' in update['message']:
                        user_id = update['message']['from']['id']
                        if user_id not in ids:
                            ids.append(user_id)

                logging.info(f"IDs de usuarios obtenidos: {ids}")
                return ids
            else:
                logging.info("No hay interacciones recientes.")
                return []
        else:
            logging.error("Error al realizar la solicitud a la API de Telegram: %s", response.status_code)
            return []
    
    except Exception as e:
        logging.error("Error al obtener los IDs de los usuarios: %s", traceback.format_exc())
        raise  # Propaga el error para manejo en `main`

def enviar_mensaje_telegram(ids_usuarios, mensaje):
    """Envía el mensaje con la información de los radares a todos los usuarios obtenidos."""
    try:
        for user_id in ids_usuarios:
            # Construir la URL de la API de Telegram para enviar el mensaje
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            
            # Parámetros de la solicitud
            params = {
                'chat_id': user_id,
                'text': mensaje
            }
            
            # Hacer la solicitud POST para enviar el mensaje
            response = requests.post(url, data=params)
            
            if response.status_code == 200:
                logging.info(f"Mensaje enviado correctamente a {user_id}")
            else:
                logging.error(f"Error al enviar el mensaje a {user_id}: {response.status_code}")
    except Exception as e:
        logging.error("Error al enviar los mensajes de Telegram: %s", traceback.format_exc())
        raise  # Propaga el error para manejo en `main`


# def captura_mapa(driver):
#     """Toma una captura de pantalla del mapa interactivo y la guarda en la carpeta 'capturas'."""
#     try:
#         # Crear la carpeta 'capturas' si no existe
#         carpeta_destino = os.path.join(os.getcwd(), 'screenshots')
#         if not os.path.exists(carpeta_destino):
#             os.makedirs(carpeta_destino)

#         # Obtener la fecha y hora actual para crear un nombre único para la captura
#         fecha_hora = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
#         screenshot_path = os.path.join(carpeta_destino, f"screenshot_{fecha_hora}.png")

#         # Localizar el contenedor del mapa
#         mapa_elemento = driver.find_element(By.ID, "mapa")

#         # Capturar directamente el área del mapa
#         mapa_elemento.screenshot(screenshot_path)
#         logging.info(f"Captura de mapa guardada en: {screenshot_path}")

#     except Exception as e:
#         logging.error("Error al tomar la captura del mapa: %s", traceback.format_exc())

# def extraer_imagen_canvas(driver):
#     """Extrae la imagen del <canvas> dentro del contenedor del mapa y la guarda como archivo."""
#     try:
#         # Crear la carpeta 'capturas' si no existe
#         carpeta_destino = os.path.join(os.getcwd(), 'screenshots')
#         if not os.path.exists(carpeta_destino):
#             os.makedirs(carpeta_destino)

#         # Obtener la fecha y hora actual para crear un nombre único para la captura
#         fecha_hora = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
#         screenshot_path = os.path.join(carpeta_destino, f"screenshot_{fecha_hora}.png")

#         # Localizar el contenedor del mapa
#         mapa_elemento = driver.find_element(By.ID, "mapa")

#         # Buscar el canvas dentro del contenedor
#         canvas = mapa_elemento.find_element(By.TAG_NAME, "canvas")
#         logging.info("Canvas encontrado dentro del contenedor del mapa.")

#         # Cambiar el estilo del banner de cookies para asegurarse de que no está por encima del mapa
#         driver.execute_script("""
#             document.querySelector('#cookie-banner').style.zIndex = '-1';
#         """)

#         # Ejecutar JavaScript para extraer la imagen como base64
#         canvas_data_url = driver.execute_script(
#             "return arguments[0].toDataURL('image/png');", canvas
#         )

#         # Decodificar la imagen en base64 y guardarla como archivo
#         canvas_data = canvas_data_url.split(',')[1]
#         with open(carpeta_destino, "wb") as f:
#             f.write(base64.b64decode(canvas_data))
        
#         logging.info(f"Imagen del canvas guardada exitosamente en {carpeta_destino}")
    
#     except Exception as e:
#         logging.error(f"Error al extraer la imagen del canvas: {traceback.format_exc()}")

def main():
    """Función principal que inicializa el driver, carga la página, verifica el estado y envía el mensaje por WhatsApp."""

    # Inicializar el driver
    driver = inicializar_driver()

    # Cargar la página y comprobar los radares
    if driver and cargar_pagina(driver, url):
        estado_radar = comprobar_radares(driver)

        # Obtener los IDs de los usuarios
        ids_usuarios = obtener_ids_usuarios()

        if ids_usuarios:
            # Enviar la información de los radares a todos los usuarios
            enviar_mensaje_telegram(ids_usuarios, estado_radar)
        else:
            logging.info("No hay usuarios a los que enviar el mensaje.")

    # Cerrar el driver
    if driver:
        driver.quit()
        logging.info("Driver de Chrome cerrado correctamente.")

# Ejecutar el script
if __name__ == "__main__":
    main()
