import os
import socket
from unittest import mock

import pytest
import requests

import diagnostico_web as dw

# Referencia a la función real: el fixture autouse de conftest sustituye dw.sondear_red en cada test
sondear_red = dw.sondear_red

URL = "https://secreto.invalid/ruta?token=abc"


def respuesta(status=200, texto="hola", contenido=b"hola"):
    r = mock.Mock(status_code=status, text=texto, content=contenido)
    return r


@pytest.fixture
def red_ok(monkeypatch):
    """DNS, TCP, TLS y HTTP simulados y correctos."""
    monkeypatch.setattr(dw, "ip_del_runner", lambda *a, **k: "9.9.9.9")
    monkeypatch.setattr(dw.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("5.6.7.8", 443))])
    conexion = mock.MagicMock()
    monkeypatch.setattr(dw.socket, "create_connection", lambda *a, **k: conexion)
    contexto = mock.MagicMock()
    monkeypatch.setattr(dw.ssl, "create_default_context", lambda: contexto)
    monkeypatch.setattr(dw.requests, "get", lambda *a, **k: respuesta())
    return contexto


# --- sondear_red ---

def test_sonda_correcta_registra_todas_las_fases(red_ok):
    s = sondear_red(URL, {"sin": "hola", "con": "adios"})
    assert s["ok"] and s["fallo"] is None
    assert set(s["fases_ms"]) == {"dns", "tcp", "tls", "http"}
    assert (s["host"], s["ip_destino"], s["ip_runner"], s["http_status"], s["bytes"]) == (
        "secreto.invalid", "5.6.7.8", "9.9.9.9", 200, 4)
    assert s["textos_en_html"] == {"sin": True, "con": False}


def test_sonda_texto_con_entidades_html_se_reconoce(red_ok, monkeypatch):
    monkeypatch.setattr(dw.requests, "get", lambda *a, **k: respuesta(texto="ubicaci&oacute;n"))
    assert sondear_red(URL, {"t": "ubicación"})["textos_en_html"] == {"t": True}


def test_sonda_fallo_dns(red_ok, monkeypatch):
    def falla(*a, **k):
        raise socket.gaierror("sin dns")
    monkeypatch.setattr(dw.socket, "getaddrinfo", falla)
    s = sondear_red(URL)
    assert not s["ok"] and s["fallo"]["fase"] == "dns" and not s["fallo"]["timeout"]
    assert dw.clasificar_causa(s) == "red_dns"


def test_sonda_fallo_tcp_timeout(red_ok, monkeypatch):
    def falla(*a, **k):
        raise socket.timeout("timed out")
    monkeypatch.setattr(dw.socket, "create_connection", falla)
    s = sondear_red(URL)
    assert s["fallo"]["fase"] == "tcp" and s["fallo"]["timeout"]
    assert dw.clasificar_causa(s) == "red_tcp_timeout"


def test_sonda_fallo_tcp_rechazada(red_ok, monkeypatch):
    def falla(*a, **k):
        raise ConnectionRefusedError("rechazada")
    monkeypatch.setattr(dw.socket, "create_connection", falla)
    assert dw.clasificar_causa(sondear_red(URL)) == "red_tcp_rechazada"


def test_sonda_fallo_tls_cierra_la_conexion(red_ok, monkeypatch):
    conexion = mock.MagicMock()
    monkeypatch.setattr(dw.socket, "create_connection", lambda *a, **k: conexion)
    red_ok.wrap_socket.side_effect = dw.ssl.SSLError("handshake")
    s = sondear_red(URL)
    assert s["fallo"]["fase"] == "tls" and dw.clasificar_causa(s) == "red_tls"
    conexion.close.assert_called()


def test_sonda_http_timeout_y_no_filtra_la_url(red_ok, monkeypatch):
    def falla(*a, **k):
        raise requests.exceptions.ReadTimeout(f"Read timed out en {URL}")
    monkeypatch.setattr(dw.requests, "get", falla)
    s = sondear_red(URL)
    assert s["fallo"]["fase"] == "http" and s["fallo"]["timeout"]
    assert dw.clasificar_causa(s) == "http_timeout"
    assert "secreto.invalid/ruta" not in s["fallo"]["mensaje"] and "<URL>" in s["fallo"]["mensaje"]


@pytest.mark.parametrize("status", [403, 503])
def test_sonda_http_con_error_de_estado(red_ok, monkeypatch, status):
    monkeypatch.setattr(dw.requests, "get", lambda *a, **k: respuesta(status=status))
    s = sondear_red(URL)
    assert not s["ok"] and s["fallo"] is None
    assert dw.clasificar_causa(s) == f"http_{status}"


def test_sonda_nunca_lanza_con_url_invalida():
    s = sondear_red(None)
    assert not s["ok"] and s["fallo"] is not None


# --- clasificación y utilidades ---

def test_error_del_navegador_con_red_ok_se_distingue():
    sonda = {"ok": True, "fallo": None, "http_status": 200}
    assert dw.clasificar_causa(sonda, "ERR_TIMED_OUT") == "navegador_err_timed_out"


def test_contenido_no_encontrado_con_red_y_navegador_ok():
    assert dw.clasificar_causa({"ok": True, "fallo": None, "http_status": 200}) == "contenido_no_encontrado"


def test_sin_sonda():
    assert dw.clasificar_causa(None) == "causa_desconocida"
    assert dw.clasificar_causa(None, "ERR_X") == "navegador_err_x"


def test_detectar_error_navegador():
    pantalla = '<body class="neterror"><div class="error-code">ERR_TIMED_OUT</div></body>'
    assert dw.detectar_error_navegador(pantalla) == "ERR_TIMED_OUT"
    assert dw.detectar_error_navegador('<div id="main-frame-error"></div>') == "ERR_DESCONOCIDO"
    assert dw.detectar_error_navegador("<body>Hola, ERR_TIMED_OUT en un texto</body>") is None
    assert dw.detectar_error_navegador("") is None


def test_resumen_sonda():
    ok = {"fases_ms": {"dns": 200, "tcp": 100, "tls": 300, "http": 1000}, "http_status": 200, "fallo": None}
    assert "HTTP 200 en 1.0 s" in dw.resumen_sonda(ok)
    fallo = {"fallo": {"fase": "tcp", "timeout": True, "tipo": "timeout"}}
    assert "TCP (timeout)" in dw.resumen_sonda(fallo)
    assert "no disponible" in dw.resumen_sonda(None)


def test_resumen_texto_acota_y_cambia_el_hash_si_cambia_el_texto():
    largo = dw.resumen_texto("x" * 5000)
    assert len(largo["inicio"]) == dw.MAX_TEXTO_REGISTRO and largo["longitud"] == 5000
    assert dw.resumen_texto("a")["hash"] != dw.resumen_texto("b")["hash"]
    assert dw.resumen_texto("a")["hash"] == dw.resumen_texto("a")["hash"]


# --- evidencias ---

def test_evidencia_sin_directorio_no_escribe_nada():
    assert dw.guardar_evidencia(None, mock.Mock(), URL) == []


def test_evidencia_guarda_html_sin_url_y_captura(tmp_path):
    driver = mock.Mock(page_source=f"<a href='{URL}'>x</a>")
    driver.get_screenshot_as_png.return_value = b"PNG"
    escritos = dw.guardar_evidencia(str(tmp_path / "ev"), driver, URL)
    assert escritos == ["fallo.html", "fallo.png"]
    assert URL not in (tmp_path / "ev" / "fallo.html").read_text() and "<URL>" in (tmp_path / "ev" / "fallo.html").read_text()
    assert (tmp_path / "ev" / "fallo.png").read_bytes() == b"PNG"


def test_evidencia_si_falla_la_captura_guarda_el_html(tmp_path):
    driver = mock.Mock(page_source="<p>ok</p>")
    driver.get_screenshot_as_png.side_effect = RuntimeError("sin pantalla")
    assert dw.guardar_evidencia(str(tmp_path), driver, URL) == ["fallo.html"]


# --- registro ---

def test_construir_registro_con_run_de_actions():
    entorno = {"GITHUB_RUN_ID": "42", "GITHUB_REPOSITORY": "u/r", "GITHUB_SERVER_URL": "https://github.com"}
    r = dw.construir_registro({"resultado": "ok_sin_radares", "intentos": [{"n": 1}]}, 12.34, entorno)
    assert r["resultado"] == "ok_sin_radares" and r["duracion_s"] == 12.3 and r["causa"] is None
    assert r["run_url"] == "https://github.com/u/r/actions/runs/42" and r["intentos"] == [{"n": 1}]


def test_construir_registro_por_defecto_es_fallo_y_sin_url_fuera_de_actions():
    r = dw.construir_registro({"causa": "red_dns"}, 1, {})
    assert r["resultado"] == "fallo" and r["causa"] == "red_dns" and r["run_url"] is None
    assert os.environ is not None
