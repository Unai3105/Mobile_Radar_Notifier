import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from twilio.rest import Client
import logging
import time

# Configuración básica de logueo
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Opciones avanzadas de Chrome
chrome_options = Options()
chrome_options.add_argument("--headless")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--window-size=1920x1080")

# URL de la página que quieres monitorear
url = "https://www.donostia.eus/info/ciudadano/radar_movil.nsf/fwHome?ReadForm&idioma=cas&id=A434305381910"

# Credenciales de Twilio desde variables de entorno
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
TO_WHATSAPP_NUMBER = os.getenv("TO_WHATSAPP_NUMBER")

def inicializar_driver():
    """Inicializa y devuelve el driver de Chrome con configuraciones avanzadas."""
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        logging.info("Driver de Chrome inicializado exitosamente en modo headless.")
        return driver
    except Exception as e:
        logging.error(f"Error al inicializar el driver de Chrome: {e}")
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
    logging.error("No se pudo cargar la página después de múltiples intentos.")
    return False

def comprobar_radares(driver):
    """Verifica si hay radares móviles planificados para hoy y devuelve el estado como texto."""
    try:
        elementos_span12 = driver.find_elements(By.CLASS_NAME, "span12")
        
        radar_encontrado = False
        for elemento in elementos_span12:
            parrafos = elemento.find_elements(By.TAG_NAME, "p")
            for parrafo in parrafos:
                estado_texto = parrafo.text
                if "No hay ninguna ubicación planificada para hoy." in estado_texto:
                    logging.info("No hay ninguna ubicación planificada para hoy.")
                    return "No hay ninguna ubicación planificada para hoy."
                    radar_encontrado = True
                    break
            if radar_encontrado:
                break

        if not radar_encontrado:
            logging.info("Puede que haya ubicaciones de radar planificadas.")
            return "Puede que haya ubicaciones de radar planificadas."
    
    except Exception as e:
        logging.error(f"Error al comprobar el estado de los radares: {e}")
        return "Estado de radar desconocido."

def enviar_mensaje_whatsapp(mensaje):
    """Envía el mensaje especificado por WhatsApp usando Twilio y verifica su estado."""
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    try:
        # Enviar el mensaje y guardar el SID del mensaje
        message = client.messages.create(
            body=mensaje,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=TO_WHATSAPP_NUMBER
        )
        logging.info(f"Mensaje enviado por WhatsApp: {message.sid}")
        
        # Esperar unos segundos antes de verificar el estado
        time.sleep(5)  # Esto permite que el estado del mensaje se actualice en Twilio

        # Obtener el estado del mensaje
        message_status = client.messages(message.sid).fetch().status
        if message_status == 'delivered':
            logging.info("El mensaje fue entregado con éxito.")
        elif message_status in ['failed', 'undelivered']:
            logging.error(f"El mensaje no se entregó correctamente. Estado: {message_status}")
            raise Exception("Error en la entrega del mensaje de WhatsApp")  # Provoca el fallo en GitHub Actions
        elif message_status == 'queued':
            logging.info("El mensaje está en cola y será entregado pronto.")
        elif message_status == 'sent':
            logging.info("El mensaje ha sido enviado, pero la entrega aún no se ha confirmado.")
        else:
            logging.warning(f"Estado desconocido del mensaje: {message_status}")

    except Exception as e:
        logging.error(f"Error al enviar mensaje por WhatsApp: {e}")
        raise  # Esto asegura que el error se propague a GitHub Actions

def main():
    """Función principal que inicializa el driver, carga la página, verifica el estado y envía el mensaje por WhatsApp."""
    driver = inicializar_driver()
    if driver and cargar_pagina(driver, url):
        estado_radar = comprobar_radares(driver)
        enviar_mensaje_whatsapp(estado_radar)
    if driver:
        driver.quit()
        logging.info("Driver de Chrome cerrado correctamente.")

# Ejecutar el script
if __name__ == "__main__":
    main()
