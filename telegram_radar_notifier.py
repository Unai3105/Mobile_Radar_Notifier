import base64
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from pymongo import MongoClient
from selenium import webdriver
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image, ImageChops
import traceback
import requests
import logging
import time
import html
import sys
import os

# Configuración básica de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Opciones avanzadas de Chrome
chrome_options = Options()
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.add_argument("--force-device-scale-factor=2")  # Captura del mapa en alta resolución

# URL de la página a monitorear
donosti_radar_web_url = os.getenv("DONOSTI_RADAR_WEB")

# Configuración del token de tu bot y la URL de la API de Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SEND_MESSAGE_URL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
SEND_PHOTO_URL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto'

# Chat del administrador: recibe los avisos de error en lugar de los usuarios (opcional)
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# Configuración de la conexión con MongoDB Atlas
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)

# Seleccionar la base de datos y colecciones
MONGO_DB = os.getenv("MONGO_DB")
db = client[MONGO_DB]

MONGO_COLLECTION_INTERACTIONS = os.getenv("MONGO_COLLECTION_INTERACTIONS")
collection_interactions = db[MONGO_COLLECTION_INTERACTIONS]

MONGO_COLLECTION_REPORTS = os.getenv("MONGO_COLLECTION_REPORTS")
collection_reports = db[MONGO_COLLECTION_REPORTS]





###############################
##   Funciones de Scraping   ##
###############################


def inicializar_driver():
    """Inicializa y devuelve el driver de Chrome con configuraciones avanzadas."""
    try:
        driver = webdriver.Chrome(options=chrome_options)  # Selenium Manager gestiona el chromedriver
        logging.info("Driver de Chrome inicializado exitosamente en modo headless.")
        return driver
    except Exception:
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
    logging.error("No se pudo cargar la página después de %s intentos.", max_retries)
    raise RuntimeError("No se pudo cargar la página de radares")

def rechazar_cookies(driver):
    """Busca y cierra el aviso de cookies si está presente."""
    try:
        # Esperar y buscar el botón "Rechazar todo"
        cookies_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Rechazar todo')]")
        cookies_button.click()
        logging.info("Aviso de cookies rechazado.")
        time.sleep(1)  # Esperar a que el aviso desaparezca
    except Exception:
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
    """Verifica si hay radares móviles planificados para hoy y devuelve las ubicaciones (vacías si no hay)."""
    try:
        # Esperar explícitamente a que los elementos con la clase "span12" estén presentes en el DOM
        WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.CLASS_NAME, "span12"))
        )

        # Buscar todos los elementos con la clase "span12"
        elementos_span12 = driver.find_elements(By.CLASS_NAME, "span12")

        # Inicializar una lista vacía para las ubicaciones
        ubicaciones = []

        # Iterar sobre los elementos encontrados
        for elemento in elementos_span12:
            # Esperar a que los párrafos dentro de cada "span12" estén presentes
            WebDriverWait(driver, 30).until(
                EC.presence_of_all_elements_located((By.TAG_NAME, "p"))
            )

            # Obtener los párrafos
            parrafos = elemento.find_elements(By.TAG_NAME, "p")

            # Buscar en cada párrafo si hay radares planificados o no
            for i, parrafo in enumerate(parrafos):
                # Reubicar el párrafo antes de interactuar con él
                parrafo = elemento.find_elements(By.TAG_NAME, "p")[i]
                texto_parrafo = parrafo.text

                # Caso en que no hay radares para hoy
                if "No hay ninguna ubicación planificada para hoy." in texto_parrafo:
                    logging.info("No hay radares móviles planificados para hoy.")
                    return ubicaciones  # Retornar lista vacía

                # Caso en que hay radares planificados para hoy
                elif "el radar móvil estará operando en las siguientes ubicaciones" in texto_parrafo:
                    # Verificar si hay al menos un párrafo siguiente para evitar IndexError
                    if i + 1 < len(parrafos):
                        # Reubicar los elementos antes de acceder a ellos
                        ubicaciones = [span.text for span in parrafos[i + 1].find_elements(By.CLASS_NAME, "label")]
                        logging.info(f"Radares móviles encontrados: {ubicaciones}")
                        return ubicaciones  # Retornar las ubicaciones encontradas

        # Si no se encontró nada relevante, la web ha cambiado o no ha cargado bien
        logging.warning("Estado de radares desconocido o no planificado.")
        return None

    except Exception:
        logging.error("Error al comprobar los radares: %s", traceback.format_exc())
        raise  # Propaga el error al `main`
        
# JS que localiza la instancia del mapa OpenLayers, encuadra la vista en los
# tramos dibujados (capas vectoriales) y espera a que el mapa termine de pintar.
JS_ENCUADRAR_RADARES = """
const done = arguments[arguments.length - 1];
const esMapa = o => o && typeof o.getView === 'function' && typeof o.getLayers === 'function'
                    && typeof o.getTargetElement === 'function';
const buscarMapa = () => {
  // 1) Visor Bisorea (geo.euskadi.eus): COREProxy.listeners[i].mapMng.map
  try {
    const ls = (window.COREProxy && window.COREProxy.listeners) || [];
    for (const l of Object.values(ls)) {
      const m = l && l.mapMng && l.mapMng.map;
      if (esMapa(m)) return m;
    }
  } catch (e) { /* sigue con la búsqueda genérica */ }
  // 2) Búsqueda genérica en anchura desde window
  const vistos = new Set();
  const cola = [[window, 0]];
  while (cola.length) {
    const [obj, nivel] = cola.shift();
    if (!obj || vistos.has(obj)) continue;
    vistos.add(obj);
    let claves = [];
    try { claves = Object.keys(obj); } catch (e) { continue; }
    for (const k of claves) {
      if (obj === window && /^(document|parent|top|self|frames|window)$/.test(k)) continue;
      let v; try { v = obj[k]; } catch (e) { continue; }
      if (!v || (typeof v !== 'object' && typeof v !== 'function')) continue;
      if (esMapa(v)) return v;
      if (nivel < 4 && vistos.size < 20000) cola.push([v, nivel + 1]);
    }
  }
  return null;
};

// Extensión de todas las features vectoriales visibles (los tramos rojos)
const capasHoja = mapa => {
  const out = [];
  const rec = capas => capas.forEach(c => c.getLayers ? rec(c.getLayers().getArray()) : out.push(c));
  rec(mapa.getLayers().getArray());
  return out;
};
const extension = mapa => {
  let ext = null;
  capasHoja(mapa).forEach(capa => {
    if (capa.getVisible && !capa.getVisible()) return;
    const src = capa.getSource && capa.getSource();
    if (!src || typeof src.getFeatures !== 'function') return;
    src.getFeatures().forEach(f => {
      const g = f.getGeometry && f.getGeometry();
      if (!g) return;
      const e = g.getExtent();
      ext = ext ? [Math.min(ext[0], e[0]), Math.min(ext[1], e[1]),
                   Math.max(ext[2], e[2]), Math.max(ext[3], e[3])] : e.slice();
    });
  });
  return ext && isFinite(ext[0]) ? ext : null;
};

// Encuadra y espera a que las teselas terminen de cargar (OL 4 no tiene 'rendercomplete')
const encuadrar = (mapa, ext) => {
  let terminado = false;
  const fin = r => { if (!terminado) { terminado = true; done(r); } };
  let pendientes = 0;
  let quieto = null;
  const revisar = () => {
    clearTimeout(quieto);
    if (pendientes <= 0) quieto = setTimeout(() => fin('ok'), 1200);
  };
  capasHoja(mapa).forEach(capa => {
    const src = capa.getSource && capa.getSource();
    if (!src || !src.on) return;
    src.on('tileloadstart', () => { pendientes++; clearTimeout(quieto); });
    ['tileloadend', 'tileloaderror'].forEach(ev => src.on(ev, () => { pendientes--; revisar(); }));
  });
  setTimeout(() => fin('timeout_render'), 15000);
  // Mapa más alto (proporción 4:3) para que los tramos salgan más ampliados
  const destino = mapa.getTargetElement();
  if (destino) destino.style.height = Math.round(destino.clientWidth * 0.75) + 'px';
  mapa.updateSize();
  mapa.getView().fit(ext, { padding: [40, 40, 40, 40], maxZoom: 17, duration: 0,
                            constrainResolution: false });
  mapa.renderSync && mapa.renderSync();
  revisar();
};

// Las features se añaden cuando el visor termina de cargar: reintentar hasta 15 s
const inicio = Date.now();
const intentar = () => {
  const mapa = buscarMapa();
  const ext = mapa && extension(mapa);
  if (ext) { encuadrar(mapa, ext); return; }
  if (Date.now() - inicio > 15000) { done(mapa ? 'sin_features' : 'sin_mapa'); return; }
  setTimeout(intentar, 500);
};
intentar();
"""


def recortar_zona_roja(img, margen=60, min_lado=400):
    """Recorta la imagen a la zona que contiene los trazos rojos (tramos con radar).

    Si no se detectan píxeles rojos devuelve la imagen original.
    """
    rgb = img.convert("RGB")
    ancho, alto = rgb.size
    # Rojo "puro" de los tramos: R alto, G y B bajos
    r, g, b = rgb.split()
    mascara = Image.eval(r, lambda v: 255 if v > 170 else 0)
    mascara = ImageChops.multiply(mascara, Image.eval(g, lambda v: 255 if v < 90 else 0))
    mascara = ImageChops.multiply(mascara, Image.eval(b, lambda v: 255 if v < 90 else 0))
    bbox = mascara.getbbox()
    if not bbox:
        return img

    left, top, right, bottom = bbox
    left, top = max(0, left - margen), max(0, top - margen)
    right, bottom = min(ancho, right + margen), min(alto, bottom + margen)

    # Garantizar un tamaño mínimo para no quedarnos con un recorte diminuto
    def ampliar(a, b, total):
        falta = min_lado - (b - a)
        if falta > 0:
            a = max(0, a - falta // 2)
            b = min(total, a + min_lado)
            a = max(0, b - min_lado)
        return a, b

    left, right = ampliar(left, right, ancho)
    top, bottom = ampliar(top, bottom, alto)
    return img.crop((left, top, right, bottom))


def capturar_elemento(driver, elemento, escala=1):
    """Captura un elemento a resolución real (devicePixelRatio) usando CDP.

    ``element.screenshot_as_png`` devuelve la imagen a 1x aunque Chrome
    renderice a 2x, así que se recurre a ``Page.captureScreenshot`` con clip.
    """
    try:
        r = driver.execute_script(
            "const r = arguments[0].getBoundingClientRect();"
            "return [r.left + scrollX, r.top + scrollY, r.width, r.height];",
            elemento,
        )
        datos = driver.execute_cdp_cmd("Page.captureScreenshot", {
            "format": "png",
            "captureBeyondViewport": True,
            "clip": {"x": r[0], "y": r[1], "width": r[2], "height": r[3], "scale": escala},
        })
        return Image.open(BytesIO(base64.b64decode(datos["data"])))
    except Exception:
        logging.warning("Captura CDP fallida, se usa la de Selenium: %s", traceback.format_exc())
        return Image.open(BytesIO(elemento.screenshot_as_png))


def extraer_canvas(driver):
    """Captura solo el mapa, encuadrado y ampliado en los tramos con radar, como imagen en memoria."""
    try:
        # Rechazar cookies si es necesario
        rechazar_cookies(driver)

        # Esperar a que el mapa (OpenLayers) exista y ocultar sus controles
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".ol-viewport canvas"))
        )
        ocultar_elementos(driver)

        viewport = driver.find_element(By.CSS_SELECTOR, ".ol-viewport")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", viewport)

        # Encuadrar el mapa en los tramos y esperar a que termine de renderizar
        driver.set_script_timeout(40)
        try:
            resultado = driver.execute_async_script(JS_ENCUADRAR_RADARES)
        except Exception as e:
            resultado = f"error_js: {e}"
        logging.info(f"Encuadre del mapa en los radares: {resultado}")
        if resultado != "ok":
            # Sin acceso a la API del mapa: margen para que carguen las teselas
            time.sleep(5)

        # Captura únicamente del elemento del mapa (no de toda la web)
        img = capturar_elemento(driver, viewport)

        # Recortar a la zona de los trazos rojos (refuerza/sustituye al encuadre JS)
        img = recortar_zona_roja(img)

        img_byte_array = BytesIO()
        img.save(img_byte_array, format="PNG")
        img_byte_array.seek(0)

        logging.info(f"Mapa capturado ({img.size[0]}x{img.size[1]}) y convertido en imagen en memoria.")
        return img_byte_array
    except Exception:
        logging.error("Error al extraer el canvas: %s", traceback.format_exc())
        raise # Propaga el error al `main`





###############################
##   Funciones de Telegram   ##
###############################


def enviar_mensaje_telegram(ids_usuarios, has_radar, locations):
    """Envía el mensaje con la información de los radares a todos los usuarios obtenidos."""

    # Inicializar variables para el mensaje y las listas de usuarios
    message_sent = ""
    ids_sent = []
    ids_error = []

    try:
        if has_radar:
            # Mensaje para radares encontrados (HTML escapado para evitar errores con caracteres especiales)
            message_sent = "🚨 El radar móvil estará operando en las siguientes ubicaciones:\n\n"
            message_sent += "\n".join([f"   •  <b>{html.escape(loc)}</b>" for loc in locations])
            message_sent += "\n\n🚗💨 ¡Cuidado con los naranjitos! 🚓"
        else:
            # Mensaje cuando no hay radares
            message_sent = "No hay radares móviles planificados para hoy."

        # Usar una sesión para las solicitudes (opcionalmente mejora el rendimiento)
        with requests.Session() as session:
            for user_id in ids_usuarios:
                params = {
                    'chat_id': user_id,
                    'text': message_sent,
                    'parse_mode': 'HTML'
                }

                # Enviar el mensaje
                response = session.post(SEND_MESSAGE_URL, data=params, timeout=30)

                if response.status_code == 200:
                    ids_sent.append(user_id)  # Añadir id del usuario al que se le envió el mensaje
                    logging.info(f"Mensaje enviado correctamente a {user_id}")
                else:
                    ids_error.append(user_id)   # Añadir id del usuario al que NO se le pudo enviar el mensaje
                    logging.error(f"Error al enviar el mensaje a {user_id}: {response.status_code}")
                    if response.status_code == 403:
                        # El usuario ha bloqueado el bot: no volver a enviarle mensajes
                        marcar_usuario_inactivo(user_id)

    except Exception:
        logging.error("Error al enviar los mensajes de Telegram: %s", traceback.format_exc())
        raise  # Propaga el error al `main`

    return message_sent, ids_sent, ids_error

def enviar_imagen_telegram(ids_usuarios, img_byte_array):
    """Envía la imagen (como archivo) a los usuarios obtenidos."""
    try:
        for user_id in ids_usuarios:
            # Parámetros de la solicitud
            params = {
                'chat_id': user_id,
            }
            
            # Rebobinar el puntero del archivo antes de cada solicitud
            img_byte_array.seek(0)  # Aseguramos que el puntero apunte al inicio del archivo
            
            # Enviar la imagen como un archivo en memoria
            files = {
                'photo': ('mapa_recortado.png', img_byte_array, 'image/png')
            }

            # Hacer la solicitud POST para enviar la imagen
            response = requests.post(SEND_PHOTO_URL, data=params, files=files, timeout=60)

            if response.status_code == 200:
                logging.info(f"Imagen enviada correctamente a {user_id}")
            else:
                logging.error(f"Error al enviar la imagen a {user_id}: {response.status_code}")
    except Exception:
        logging.error("Error al enviar las imágenes de Telegram: %s", traceback.format_exc())
        raise  # Propaga el error al `main`





##############################
##   Funciones de MongoDB   ##
##############################


def enviar_aviso_admin(texto):
    """Envía un aviso de error solo al administrador (si ADMIN_CHAT_ID está configurado)."""
    if not ADMIN_CHAT_ID:
        logging.warning("ADMIN_CHAT_ID no configurado: no se envía el aviso al administrador.")
        return
    try:
        requests.post(SEND_MESSAGE_URL, data={'chat_id': ADMIN_CHAT_ID, 'text': texto}, timeout=30)
    except Exception:
        logging.error("Error al enviar el aviso al administrador: %s", traceback.format_exc())

def marcar_usuario_inactivo(user_id):
    """Marca como inactivo a un usuario que ha bloqueado el bot."""
    collection_interactions.update_one({"chat_id": user_id}, {"$set": {"active": False}})
    logging.info(f"Usuario {user_id} marcado como inactivo (ha bloqueado el bot).")

def obtener_ids_usuarios():
    """Obtiene los IDs de los usuarios activos que han interactuado con el bot."""
    try:
        # Buscar los usuarios no marcados como inactivos
        usuarios = collection_interactions.find({"active": {"$ne": False}})

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

def registrar_monitoreo_mensajes(scrapping_time, has_radar, locations, message_sent, ids_sent, ids_error):
    """Registra en MongoDB el monitoreo de los mensajes enviados tras el scraping."""
    try:

        # Documento a insertar
        documento = {
            "scrapping_time": scrapping_time,
            "has_radar": has_radar,
            "locations": locations,
            "message_sent": message_sent,
            "ids_sent": ids_sent,
            "ids_error":ids_error
        }

        # Inserta el documento
        result = collection_reports.insert_one(documento)
        logging.info(f"Monitoreo del scrapping realizada correctamente con ID: {result.inserted_id}")

    except Exception:
        logging.error("Error al registrar el monitoreo en MongoDB: %s", traceback.format_exc())
        raise # Propaga el error al `main`





########################
##    Funcion Main    ##
########################


def main():
    """Función principal que inicializa el driver, carga la página, verifica el estado y envía el mensaje por Telegram."""

    # Inicializar el driver
    driver = inicializar_driver()
    if not driver:
        enviar_aviso_admin("⚠️ Mobile Radar Notifier: no se pudo inicializar Chrome.")
        sys.exit(1)

    try:
        # Cargar la página y comprobar el estado de los radares
        cargar_pagina(driver, donosti_radar_web_url)
        locations = comprobar_radares(driver)

        # Si no se pudo leer la web, avisar solo al administrador y terminar con error
        if locations is None:
            registrar_monitoreo_mensajes(
                scrapping_time=datetime.now(timezone.utc).isoformat(),
                has_radar=False,
                locations=None,
                message_sent="",
                ids_sent=[],
                ids_error=[]
            )
            enviar_aviso_admin("⚠️ Mobile Radar Notifier: no se pudo verificar si hay radares. Puede que la web haya cambiado.")
            sys.exit(1)

        # Obtener los IDs de los usuarios
        ids_usuarios = obtener_ids_usuarios()

        # Inicializar variables para el monitoreo
        has_radar = bool(locations)
        message_sent = ""
        ids_sent = []
        ids_error = []

        if ids_usuarios:

            if has_radar:
                # Extraer imagen del mapa de los radares
                img_byte_array = extraer_canvas(driver)

            # Enviar la información de los radares a todos los usuarios
            message_sent, ids_sent, ids_error = enviar_mensaje_telegram(ids_usuarios, has_radar, locations)

            if has_radar:
                # Enviar la imagen solo a los usuarios que recibieron el mensaje
                enviar_imagen_telegram(ids_sent, img_byte_array)

        else:
            logging.info("No hay usuarios a los que enviar el mensaje.")

        # Registrar los resultados del monitoreo en MongoDB
        registrar_monitoreo_mensajes(
            scrapping_time=datetime.now(timezone.utc).isoformat(),
            has_radar=has_radar,
            locations=locations,
            message_sent=message_sent,
            ids_sent=ids_sent,
            ids_error=ids_error
        )

    except Exception:
        enviar_aviso_admin("⚠️ Mobile Radar Notifier: la ejecución ha fallado. Revisa GitHub Actions.")
        raise

    finally:
        # Cerrar el driver siempre, aunque haya errores
        driver.quit()
        logging.info("Driver de Chrome cerrado correctamente.")






###########################
##    Ejecutar Script    ##
###########################


# Ejecutar el script
if __name__ == "__main__":
    main()
