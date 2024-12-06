from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium import webdriver
from datetime import datetime
from pymongo import MongoClient
from io import BytesIO
from PIL import Image
import traceback
import requests
import logging
import time
import os

# Configuración básica de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Opciones avanzadas de Chrome
chrome_options = Options()
chrome_options.add_argument("--headless")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--window-size=1920x1080")

# URL de la página que quieres monitorear
donosti_radar_web_url = os.getenv("DONOSTI_RADAR_WEB")

# Configura el token de tu bot y la URL de la API de Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Configura la conexión con MongoDB Atlas
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)

# Selecciona la base de datos y colección
MONGO_DB = os.getenv("MONGO_DB")
db = client[MONGO_DB]
MONGO_COLLECTION_INTERACTIONS = "bot_interactions"
collection = db[MONGO_COLLECTION_INTERACTIONS]

def inicializar_driver():
    """Inicializa y devuelve el driver de Chrome con configuraciones avanzadas."""
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        logging.info("Driver de Chrome inicializado exitosamente en modo headless.")
        return driver
    except Exception as e:
        logging.error("Error al inicializar el driver de Chrome: %s", traceback.format_exc())
        return None

def cargar_pagina(driver, donosti_radar_web_url, max_retries=3):
    """Carga la página y reintenta en caso de fallos."""
    for attempt in range(max_retries):
        try:
            driver.get(donosti_radar_web_url)
            logging.info(f"Página cargada correctamente en el intento {attempt + 1}.")
            return True
        except Exception as e:
            logging.warning(f"Error al cargar la página: {e}. Reintentando ({attempt + 1}/{max_retries})...")
            time.sleep(2)
    logging.error("No se pudo cargar la página después de múltiples intentos: %s", traceback.format_exc())
    raise # Propaga el error al `main`

def rechazar_cookies(driver):
    """Busca y cierra el aviso de cookies si está presente."""
    try:
        # Esperar y buscar el botón "Rechazar todo"
        cookies_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Rechazar todo')]")
        cookies_button.click()
        logging.info("Aviso de cookies rechazado.")
        time.sleep(1)  # Esperar a que el aviso desaparezca
    except Exception as e:
        logging.warning("No se encontró el aviso de cookies o no se pudo cerrar: %s", traceback.format_exc())

def ocultar_elementos(driver):
    """Oculta los elementos no deseados en la página."""
    try:
        # Buscar el div de las capas base y ocultarlo
        base_layer_element = driver.find_element(By.ID, "SimpleBaseLayerSelectPlugin_c")
        driver.execute_script("arguments[0].style.display = 'none';", base_layer_element)

        # Buscar el div de las atribuciones y ocultarlo
        attribution_element = driver.find_element(By.CSS_SELECTOR, ".ol-attribution")
        driver.execute_script("arguments[0].style.display = 'none';", attribution_element)

        # Buscar el div del control de zoom y ocultarlo
        zoom_control_element = driver.find_element(By.CSS_SELECTOR, ".ol-zoom")
        driver.execute_script("arguments[0].style.display = 'none';", zoom_control_element)

        logging.info("Elementos ocultos exitosamente.")
    except Exception as e:
        logging.error(f"Error al ocultar los elementos: {e}")
        raise # Propaga el error al `main`

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
                    logging.info("No hay radares móviles planificados para hoy.")
                    return "No hay radares móviles planificados para hoy."

                elif fecha_actual in texto_parrafo and "el radar móvil estará operando en las siguientes ubicaciones" in texto_parrafo:
                    if i + 1 < len(parrafos):
                        ubicaciones = [span.text for span in parrafos[i + 1].find_elements(By.CLASS_NAME, "label")]
                        logging.info(f"Radares móviles encontrados: {ubicaciones}")
                        return ubicaciones

        logging.warning("Estado de radares desconocido.")
        return "Estado de radares desconocido."
    except Exception as e:
        logging.error("Error al comprobar los radares: %s", traceback.format_exc())
        raise # Propaga el error al `main`

def obtener_ids_usuarios():
    """Obtiene los IDs de los usuarios que han interactuado con el bot."""
    try:
        # Buscar todos los documentos en la colección bot_interactions
        usuarios = collection.find({})

        # Extraer los chat_id de los usuarios
        ids = []
        for usuario in usuarios:
            chat_id = usuario.get('chat_id')
            if chat_id:
                ids.append(chat_id)

        logging.info(f"IDs de usuarios obtenidos desde MongoDB: {ids}")
        return ids
    except Exception as e:
        logging.error("Error al obtener los IDs de los usuarios desde MongoDB: %s", e)
        raise # Propaga el error al `main`

def enviar_mensaje_telegram(ids_usuarios, estado_radar, ubicaciones=None):
    """Envía el mensaje con la información de los radares a todos los usuarios obtenidos."""
    try:
        for user_id in ids_usuarios:
            if estado_radar == "No hay radares móviles planificados para hoy.":
                mensaje = estado_radar
            else:
                # Construir el mensaje de ubicaciones de radares
                mensaje = "🚨 El radar móvil estará operando en las siguientes ubicaciones:\n\n"
                ubicaciones = ['Paseo de la Fe', 'Paseo de Oriamendi', 'Paseo de la Concha']
                for i, ubicacion in enumerate(ubicaciones, 1):
                    mensaje += f"   •  *{ubicacion}*\n"
                mensaje += "\n🚗💨 ¡Cuidado con los naranjitos! 🚓"

            # Construir la URL de la API de Telegram para enviar el mensaje
            sendMessage_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            
            # Parámetros de la solicitud
            params = {
                'chat_id': user_id,
                'text': mensaje,
                'parse_mode': 'Markdown'
            }
            
            # Hacer la solicitud POST para enviar el mensaje
            response = requests.post(sendMessage_url, data=params)
            
            if response.status_code == 200:
                logging.info(f"Mensaje enviado correctamente a {user_id}")
            else:
                logging.error(f"Error al enviar el mensaje a {user_id}: {response.status_code}")
    except Exception as e:
        logging.error("Error al enviar los mensajes de Telegram: %s", traceback.format_exc())
        raise # Propaga el error al `main`

def extraer_canvas(driver):
    """Extrae el contenido del canvas de la página y lo devuelve como un objeto de imagen en memoria."""
    try:
        # Rechazar cookies si es necesario
        rechazar_cookies(driver)

        # Ocultar elementos no deseados del mapa
        ocultar_elementos(driver)

        # Localizar el elemento canvas
        canvas = driver.find_element(By.CSS_SELECTOR, "canvas.ol-unselectable")

        # Desplazar la página hasta que el canvas sea visible
        driver.execute_script("arguments[0].scrollIntoView(true);", canvas)
        logging.info("Canvas desplazado a la vista.")

        # Esperar un momento para asegurarnos de que todo se haya renderizado
        time.sleep(2)

        # Tomar la captura de pantalla completa como objeto binario (para no guardarla)
        screenshot = driver.get_screenshot_as_png()
        
        # Abrir la captura desde los datos en memoria
        img = Image.open(BytesIO(screenshot))
        
        # Obtener el tamaño de la imagen original
        width, height = img.size

        # Definir el recorte
        left = 31
        top = 1
        right = width - 48
        bottom = height - 52

        # Recortar la imagen
        img_recortada = img.crop((left, top, right, bottom))

        # Guardamos la imagen en un buffer de memoria
        img_byte_array = BytesIO()
        img_recortada.save(img_byte_array, format="PNG")
        img_byte_array.seek(0)  # Rebobinar el buffer hasta el principio

        logging.info("Canvas capturado y convertido en imagen en memoria.")
        return img_byte_array  # Retorna el buffer en memoria
    except Exception as e:
        logging.error("Error al extraer el canvas: %s", traceback.format_exc())
        raise # Propaga el error al `main`

def enviar_imagen_telegram(ids_usuarios, img_byte_array):
    """Envía la imagen (como archivo) a los usuarios obtenidos."""
    try:
        for user_id in ids_usuarios:
            # Construir la URL de la API de Telegram para enviar la imagen
            sendPhoto_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            
            # Parámetros de la solicitud
            params = {
                'chat_id': user_id,
            }
            
            # Enviar la imagen como un archivo en memoria
            files = {
                'photo': ('mapa_recortado.png', img_byte_array, 'image/png')
            }

            # Hacer la solicitud POST para enviar la imagen
            response = requests.post(sendPhoto_url, data=params, files=files)

            if response.status_code == 200:
                logging.info(f"Imagen enviada correctamente a {user_id}")
            else:
                logging.error(f"Error al enviar la imagen a {user_id}: {response.status_code}")
    except Exception as e:
        logging.error("Error al enviar las imágenes de Telegram: %s", traceback.format_exc())
        raise # Propaga el error al `main`

def main():
    """Función principal que inicializa el driver, carga la página, verifica el estado y envía el mensaje por WhatsApp."""

    # Inicializar el driver
    driver = inicializar_driver()

    # Cargar la página, comprobar los radares y enviar la información a los usuarios
    if driver and cargar_pagina(driver, donosti_radar_web_url):

        # Comprobar el estado de los radares
        estado_radar = comprobar_radares(driver)

        # Obtener los IDs de los usuarios
        ids_usuarios = obtener_ids_usuarios()

        if ids_usuarios:

            # Enviar la información de los radares a todos los usuarios
            enviar_mensaje_telegram(ids_usuarios, estado_radar)

            if estado_radar != "No hay radares móviles planificados para hoy.":
                # Extraer imagen del mapa de los radares
                img_byte_array = extraer_canvas(driver)

                # Enviar la imagen a todos los usuarios
                enviar_imagen_telegram(ids_usuarios, img_byte_array)

        else:
            logging.info("No hay usuarios a los que enviar el mensaje.")

    # Cerrar el driver
    if driver:
        driver.quit()
        logging.info("Driver de Chrome cerrado correctamente.")

# Ejecutar el script
if __name__ == "__main__":
    main()
