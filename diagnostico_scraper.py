"""Diagnóstico manual del scraper: sonda de red + scraping real. NO envía nada a nadie.

Se ejecuta solo desde el workflow `diagnostico_scraper.yml` (workflow_dispatch).
No imprime la URL (es un secreto); solo el host.
"""
import os
import socket
import ssl
import time
from urllib.parse import urlparse

import requests

# El módulo del notificador lee estas variables al importarse: valores ficticios,
# sin ADMIN_CHAT_ID (no se avisa a nadie) y sin conexión real a Mongo/Telegram.
for nombre in ("TELEGRAM_TOKEN", "MONGO_URI", "MONGO_DB",
               "MONGO_COLLECTION_INTERACTIONS", "MONGO_COLLECTION_REPORTS"):
    os.environ.setdefault(nombre, "mongodb://localhost:1/x" if nombre == "MONGO_URI" else "dummy")
os.environ.pop("ADMIN_CHAT_ID", None)

URL = os.environ["DONOSTI_RADAR_WEB"]


def sonda_red(muestras=3, pausa=5):
    host = urlparse(URL).hostname
    print(f"== Sonda de red a {host} ==", flush=True)
    try:
        print("IP pública del runner:", requests.get("https://api.ipify.org", timeout=10).text, flush=True)
    except Exception as e:
        print("IP del runner: no disponible:", type(e).__name__, flush=True)
    for i in range(muestras):
        fases = {}
        try:
            t = time.monotonic()
            ip = socket.gethostbyname(host)
            fases["dns"] = round(time.monotonic() - t, 2)
            t = time.monotonic()
            s = socket.create_connection((ip, 443), timeout=15)
            fases["tcp"] = round(time.monotonic() - t, 2)
            t = time.monotonic()
            ctx = ssl.create_default_context()
            s = ctx.wrap_socket(s, server_hostname=host)
            fases["tls"] = round(time.monotonic() - t, 2)
            s.close()
            t = time.monotonic()
            r = requests.get(URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            fases["http"] = round(time.monotonic() - t, 2)
            print(f"[{i + 1}] ip={ip} {fases} status={r.status_code} bytes={len(r.content)}", flush=True)
        except Exception as e:
            print(f"[{i + 1}] FALLO tras {fases}: {type(e).__name__}: {e}".replace(URL, "<URL>"), flush=True)
        time.sleep(pausa)


def scraping_real():
    print("== Scraping real (comprobar_radares) ==", flush=True)
    import telegram_radar_notifier as n
    n._inicio_presupuesto = time.monotonic()
    driver = n.inicializar_driver()
    if not driver:
        print("RESULTADO: no se pudo iniciar Chrome")
        return
    try:
        n.cargar_pagina(driver, URL)
        ubicaciones = n.comprobar_radares(driver)
        print("RESULTADO comprobar_radares:", ubicaciones, flush=True)
        if ubicaciones:
            img = n.extraer_canvas(driver)
            print("RESULTADO captura mapa: OK,", len(img.getvalue()), "bytes", flush=True)
    except Exception as e:
        print("RESULTADO: FALLO", type(e).__name__, str(e).replace(URL, "<URL>"), flush=True)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sonda_red()
    scraping_real()
