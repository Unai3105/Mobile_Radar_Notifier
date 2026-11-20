"""Diagnóstico de la web de radares: sonda de red por fases, clasificación de la causa de un
fallo, evidencias y construcción del registro de cada ejecución.

Sin efectos al importarse (no toca Mongo, Telegram ni el entorno) para poder probarlo aislado.
Nunca se escribe la URL (es un secreto): se sustituye por ``<URL>`` en todo lo que sale de aquí.
"""
import hashlib
import html as html_lib
import logging
import os
import re
import socket
import ssl
import time
import traceback
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import requests

USER_AGENT = "Mozilla/5.0 (compatible; MobileRadarNotifier-diagnostico)"
# Máximo de caracteres del texto de la web que se guarda en el registro (solo en Mongo)
MAX_TEXTO_REGISTRO = 500


def _fragmentos_secretos(url):
    """Formas en que puede aparecer la URL (o su ruta y query) en un texto: literal, con barras
    escapadas, codificada en %, con entidades HTML y sin esquema. Del más largo al más corto."""
    if not url:
        return []
    partes = urlparse(url)
    base = [url, url.rstrip("/")]
    if partes.hostname:
        sin_esquema = url.split("://", 1)[-1]
        base += [sin_esquema, sin_esquema.rstrip("/")]
    ruta_query = partes.path + (f"?{partes.query}" if partes.query else "")
    if len(ruta_query.strip("/")) >= 3:  # una ruta trivial ("/") no es secreta ni distintiva
        base += [ruta_query, ruta_query.rstrip("/")]
    if partes.query:
        base.append(partes.query)
    formas = set()
    for f in base:
        formas.update({f, f.replace("/", "\\/"), quote(f, safe=""), quote(f), html_lib.escape(f)})
    return sorted((f for f in formas if f), key=len, reverse=True)


def ocultar_url(texto, url):
    """Convierte a str y oculta la URL secreta y sus partes (ruta, query) en cualquier forma habitual."""
    texto = str(texto)
    for fragmento in _fragmentos_secretos(url):
        texto = texto.replace(fragmento, "<URL>")
    return texto


def _es_timeout(exc):
    return (isinstance(exc, (socket.timeout, TimeoutError, requests.exceptions.Timeout))
            or "timed out" in str(exc).lower())


def ip_del_runner(timeout=5):
    """IP pública de salida de esta máquina (None si no se pudo averiguar)."""
    try:
        ip = requests.get("https://api.ipify.org", timeout=timeout).text.strip()
        return ip if re.fullmatch(r"[0-9a-fA-F:.]{3,45}", ip) else None
    except Exception:
        return None


def sondear_red(url, textos=None, timeout_conexion=10, timeout_http=20, limite_total=30):
    """Comprueba fase a fase (DNS, TCP, TLS, HTTP) si se llega a la web desde esta máquina.

    No lanza excepciones: devuelve siempre un diccionario con ``ok``, los tiempos por fase en
    ms (``fases_ms``) y, si algo falla, ``fallo`` con la fase, el tipo de error y si fue timeout.
    ``limite_total`` (s) acota el tiempo de las fases con red tras la resolución DNS.
    ``textos`` es un dict {nombre: texto}: indica cuáles aparecen en el HTML estático.
    Del fallo NO se guarda el mensaje de la excepción (puede contener la URL secreta): solo
    fase, tipo y si fue timeout. Tampoco se guarda el nombre del host.
    """
    sonda = {
        "momento": datetime.now(timezone.utc).isoformat(),
        "ip_runner": ip_del_runner(),
        "ip_destino": None,
        "fases_ms": {},
        "ok": False,
        "fallo": None,
        "http_status": None,
        "bytes": None,
        "textos_en_html": None,
    }
    fase = "dns"

    def medir(desde):
        return round((time.monotonic() - desde) * 1000)

    limite = time.monotonic() + limite_total

    def plazo(maximo):
        """Timeout de una fase: el suyo, o lo que quede del límite total (nunca negativo)."""
        restante = limite - time.monotonic()
        if restante <= 0:
            raise TimeoutError("límite total de la sonda agotado")
        return min(maximo, restante)

    try:
        partes = urlparse(url)
        https = partes.scheme == "https"
        puerto = partes.port or (443 if https else 80)

        t = time.monotonic()
        infos = socket.getaddrinfo(partes.hostname, puerto, type=socket.SOCK_STREAM)
        sonda["fases_ms"]["dns"] = medir(t)
        # IPv4 primero: un runner sin ruta IPv6 no debe dar un falso fallo de TCP
        direcciones = sorted({info[4][0] for info in infos},
                             key=lambda d: ":" in d)

        fase = "tcp"
        t = time.monotonic()
        conexion, ultimo_error = None, None
        for ip in direcciones:
            try:
                conexion = socket.create_connection((ip, puerto), timeout=plazo(timeout_conexion))
                sonda["ip_destino"] = ip
                break
            except Exception as e:
                ultimo_error = e
        if conexion is None:
            raise ultimo_error
        sonda["fases_ms"]["tcp"] = medir(t)
        try:
            if https:
                fase = "tls"
                t = time.monotonic()
                conexion.settimeout(plazo(timeout_conexion))
                with ssl.create_default_context().wrap_socket(conexion, server_hostname=partes.hostname):
                    pass
                sonda["fases_ms"]["tls"] = medir(t)
        finally:
            conexion.close()

        fase = "http"
        t = time.monotonic()
        respuesta = requests.get(url, timeout=(plazo(timeout_conexion), plazo(timeout_http)),
                                 headers={"User-Agent": USER_AGENT})
        sonda["fases_ms"]["http"] = medir(t)
        sonda["http_status"] = respuesta.status_code
        sonda["bytes"] = len(respuesta.content)
        if textos:
            cuerpo = html_lib.unescape(respuesta.text)
            sonda["textos_en_html"] = {nombre: texto in cuerpo for nombre, texto in textos.items()}
        sonda["ok"] = respuesta.status_code < 400
    except Exception as e:
        sonda["fallo"] = {
            "fase": fase,
            "tipo": type(e).__name__,
            "timeout": _es_timeout(e),
        }
    return sonda


def resumen_sonda(sonda):
    """Una línea legible con el resultado de la sonda (para el aviso al administrador)."""
    if not sonda:
        return "sonda de red no disponible"
    fallo = sonda.get("fallo")
    if fallo:
        detalle = "timeout" if fallo.get("timeout") else fallo.get("tipo", "error")
        return f"desde el runner falla en {fallo['fase'].upper()} ({detalle})"
    fases = sonda.get("fases_ms", {})
    tramos = " · ".join(f"{n.upper()} {fases[n] / 1000:.1f} s" for n in ("dns", "tcp", "tls") if n in fases)
    http = f"HTTP {sonda.get('http_status')}"
    if "http" in fases:
        http += f" en {fases['http'] / 1000:.1f} s"
    return f"desde el runner la red responde ({tramos} · {http})"


def detectar_error_navegador(cuerpo):
    """Código ERR_* si el HTML es la pantalla de error de Chrome; None si es una página normal."""
    if not cuerpo or ("neterror" not in cuerpo and "main-frame-error" not in cuerpo):
        return None
    codigo = re.search(r"\bERR_[A-Z0-9_]+\b", cuerpo)
    return codigo.group(0) if codigo else "ERR_DESCONOCIDO"


def clasificar_causa(sonda, error_navegador=None):
    """Causa del fallo a partir de la sonda (si existe) y del error que mostró el navegador."""
    if sonda:
        fallo = sonda.get("fallo")
        if fallo:
            fase = fallo.get("fase")
            if fase == "dns":
                return "red_dns"
            if fase == "tcp":
                return "red_tcp_timeout" if fallo.get("timeout") else "red_tcp_rechazada"
            if fase == "tls":
                return "red_tls"
            return "http_timeout" if fallo.get("timeout") else "http_error_conexion"
        estado = sonda.get("http_status")
        if estado and estado >= 400:
            return f"http_{estado}"
    if error_navegador:
        return "navegador_" + error_navegador.lower()
    if sonda:
        return "contenido_no_encontrado"
    return "causa_desconocida"


def hash_texto(texto):
    return hashlib.sha256((texto or "").encode("utf-8")).hexdigest()[:16]


def resumen_texto(texto):
    """Hash y arranque del texto que lee el scraper: detecta cambios de texto en la web."""
    texto = texto or ""
    return {"hash": hash_texto(texto), "longitud": len(texto), "inicio": texto[:MAX_TEXTO_REGISTRO]}


def guardar_evidencia(directorio, driver, url, prefijo="fallo", con_captura=False):
    """Guarda el HTML (con la URL secreta y sus partes redactadas) en ``directorio``.

    La captura de pantalla solo se guarda con ``con_captura=True``: no se puede redactar y los
    artefactos del workflow pueden ser visibles. Sin directorio no hace nada. Cada archivo es
    independiente: si uno falla, se intenta el otro. Devuelve los nombres de los archivos escritos.
    """
    if not directorio:
        return []
    escritos = []
    try:
        os.makedirs(directorio, exist_ok=True)
    except Exception:
        logging.warning("No se pudo crear el directorio de evidencias: %s", traceback.format_exc())
        return []
    try:
        ruta = os.path.join(directorio, f"{prefijo}.html")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(ocultar_url(driver.page_source, url))
        escritos.append(os.path.basename(ruta))
    except Exception:
        logging.warning("No se pudo guardar el HTML de evidencia: %s", traceback.format_exc())
    if con_captura:
        try:
            ruta = os.path.join(directorio, f"{prefijo}.png")
            with open(ruta, "wb") as f:
                f.write(driver.get_screenshot_as_png())
            escritos.append(os.path.basename(ruta))
        except Exception:
            logging.warning("No se pudo guardar la captura de evidencia: %s", traceback.format_exc())
    return escritos


def construir_registro(estado, duracion_s, entorno=None):
    """Documento que se guarda en Mongo por ejecución (buena o mala).

    ``estado`` es el diccionario que va rellenando el notificador. ``resultado`` es lo que
    devolvió el scraping; ``error`` solo aparece si algo falló después (envío, Mongo...).
    """
    entorno = os.environ if entorno is None else entorno
    run_id = entorno.get("GITHUB_RUN_ID")
    return {
        "momento": datetime.now(timezone.utc).isoformat(),
        "resultado": estado.get("resultado", "fallo"),
        "causa": estado.get("causa"),
        "modo": estado.get("modo", "normal"),
        "aviso_usuarios_fallo": bool(estado.get("aviso_usuarios_fallo")),
        "error": estado.get("error"),
        "duracion_s": round(duracion_s, 1),
        "intentos": estado.get("intentos", []),
        "sonda_inicial": estado.get("sonda_inicial"),
        "sonda_post_fallo": estado.get("sonda_post_fallo"),
        "contenido": estado.get("contenido"),
        "evidencias": estado.get("evidencias", []),
        "run_id": run_id,
        "run_url": (f"{entorno.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                    f"{entorno.get('GITHUB_REPOSITORY')}/actions/runs/{run_id}")
        if run_id and entorno.get("GITHUB_REPOSITORY") else None,
    }
