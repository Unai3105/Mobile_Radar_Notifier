import pytest
from selenium.common.exceptions import TimeoutException
from unittest import mock


def sesion_falsa(codigos, enviados):
    def post(url, data, timeout):
        enviados.append(data)
        return mock.Mock(status_code=codigos[data["chat_id"]])

    sesion = mock.MagicMock()
    sesion.__enter__.return_value.post = post
    return sesion


def test_solo_usuarios_activos(notifier):
    notifier.collection_interactions.insert_many([
        {"chat_id": 1, "active": True},
        {"chat_id": 2, "active": False},
        {"chat_id": 3},  # documentos antiguos sin campo active
    ])
    assert notifier.obtener_ids_usuarios() == [1, 3]


def test_403_marca_usuario_inactivo(notifier):
    notifier.collection_interactions.insert_many([{"chat_id": 1, "active": True}, {"chat_id": 2, "active": True}])
    enviados = []
    with mock.patch.object(notifier.requests, "Session", return_value=sesion_falsa({1: 200, 2: 403}, enviados)):
        _, ids_sent, ids_error = notifier.enviar_mensaje_telegram([1, 2], False, [])
    assert (ids_sent, ids_error) == ([1], [2])
    assert notifier.obtener_ids_usuarios() == [1]


def test_ubicaciones_se_escapan_en_html(notifier):
    enviados = []
    with mock.patch.object(notifier.requests, "Session", return_value=sesion_falsa({1: 200}, enviados)):
        mensaje, _, _ = notifier.enviar_mensaje_telegram([1], True, ["Calle <A> & B_*"])
    assert "<b>Calle &lt;A&gt; &amp; B_*</b>" in mensaje
    assert enviados[0]["parse_mode"] == "HTML"


def test_recortar_zona_roja_centra_en_trazos_rojos():
    from PIL import Image, ImageDraw
    import telegram_radar_notifier as trn

    img = Image.new("RGB", (2000, 1000), (230, 230, 230))
    ImageDraw.Draw(img).line((900, 400, 1100, 500), fill=(220, 20, 20), width=6)
    recorte = trn.recortar_zona_roja(img, margen=50, min_lado=100)
    assert recorte.size[0] < 400 and recorte.size[1] < 300


def test_recortar_zona_roja_sin_rojo_devuelve_original():
    from PIL import Image
    import telegram_radar_notifier as trn

    img = Image.new("RGB", (800, 600), (230, 230, 230))
    assert trn.recortar_zona_roja(img).size == (800, 600)


# --- Reintentos de comprobar_radares con un driver simulado ---

SIN_RADARES = "No hay ninguna ubicación planificada para hoy."


class ElementoFalso:
    def __init__(self, textos=(), html=""):
        self._textos, self._html = textos, html
        self.text = " ".join(textos)

    def find_elements(self, by, valor):
        if valor == "p":
            return [ElementoFalso([t]) for t in self._textos]
        return []

    def get_attribute(self, nombre):
        return self._html


class DriverFalso:
    """Driver simulado: "span12" siempre existe, pero su texto solo aparece tras `listo_tras`
    cargas de página (0 = ya está; None = nunca)."""

    title = "Titulo de prueba"

    def __init__(self, listo_tras=0, texto=SIN_RADARES,
                 html="<body>https://secreto.invalid/x " + "a" * 5000 + "</body>"):
        self.listo_tras, self.texto, self.html = listo_tras, texto, html
        self.esperas = []   # timeouts pedidos a WebDriverWait
        self.cargas = 0     # llamadas a driver.get

    def get(self, url):
        self.cargas += 1

    def find_elements(self, by, valor):
        if valor not in ("span12", "p"):
            return []
        listo = self.listo_tras is not None and self.cargas >= self.listo_tras
        return [ElementoFalso([self.texto] if listo else [])]

    def find_element(self, by, valor):
        return ElementoFalso(html=self.html)


@pytest.fixture
def espera_falsa(notifier, monkeypatch):
    """Sustituye WebDriverWait y el reloj: no hay esperas ni red reales.

    Cada espera consume su timeout del reloj simulado, así el presupuesto es determinista.
    """
    reloj = {"t": 1000.0}
    monkeypatch.setattr(notifier.time, "monotonic", lambda: reloj["t"])
    monkeypatch.setattr(notifier.time, "sleep", lambda s: reloj.__setitem__("t", reloj["t"] + s))

    class EsperaFalsa:
        def __init__(self, driver, timeout):
            self.driver, self.timeout = driver, timeout
            driver.esperas.append(timeout)

        def until(self, condicion):
            if condicion(self.driver):
                return True
            reloj["t"] += self.timeout
            raise TimeoutException("simulado")

    monkeypatch.setattr(notifier, "WebDriverWait", EsperaFalsa)
    monkeypatch.setattr(notifier, "_inicio_presupuesto", None)
    monkeypatch.setattr(notifier, "donosti_radar_web_url", "https://secreto.invalid/x")
    avisos = []
    monkeypatch.setattr(notifier, "enviar_aviso_admin", avisos.append)
    return avisos


def test_exito_al_primer_intento(notifier, espera_falsa):
    driver = DriverFalso(listo_tras=0)
    assert notifier.comprobar_radares(driver) == []
    assert driver.esperas[0] == 30
    assert driver.cargas == 0
    assert espera_falsa == []


def test_exito_tras_reintento_recarga_con_espera_mayor(notifier, espera_falsa):
    driver = DriverFalso(listo_tras=2)
    assert notifier.comprobar_radares(driver) == []
    assert driver.cargas == 2  # se recargó la página antes de cada reintento
    assert driver.esperas[0] == 30
    assert all(e > 30 for e in driver.esperas[1:3])  # los reintentos esperan más
    assert espera_falsa == []


def test_span12_presente_pero_contenido_vacio_se_reintenta(notifier, espera_falsa):
    """span12 existe desde el principio; el reintento debe esperar al texto, no a span12."""
    driver = DriverFalso(listo_tras=1)
    assert notifier.comprobar_radares(driver) == []
    assert driver.cargas == 1


def test_con_radares_devuelve_ubicaciones(notifier, espera_falsa):
    driver = DriverFalso(texto=notifier.TEXTO_CON_RADARES)
    assert notifier.contenido_radares_listo(driver)


def test_fallo_definitivo_avisa_al_admin_y_no_a_usuarios(notifier, espera_falsa, monkeypatch, caplog):
    llamadas = []
    monkeypatch.setattr(notifier, "enviar_mensaje_telegram", lambda *a, **k: llamadas.append(a))
    driver = DriverFalso(listo_tras=None)
    with pytest.raises(notifier.WebNoDisponible):
        notifier.comprobar_radares(driver)
    assert llamadas == []
    assert len(driver.esperas) > 1 and driver.cargas == len(driver.esperas) - 1
    assert len(espera_falsa) == 1 and "log" in espera_falsa[0]
    registro = caplog.text
    assert "Titulo de prueba" in registro
    assert "secreto.invalid" not in registro      # la URL no se vuelca
    assert "a" * 2900 in registro and "a" * 3000 not in registro  # HTML acotado


def test_texto_desconocido_nunca_da_no_hay_radares(notifier, espera_falsa):
    """Sin el texto literal no se devuelve [] ni None: se falla y se avisa al admin."""
    driver = DriverFalso(texto="no hay nada relevante")
    with pytest.raises(notifier.WebNoDisponible):
        notifier.comprobar_radares(driver)
    assert len(espera_falsa) == 1


def test_presupuesto_total_corta_los_reintentos(notifier, espera_falsa, monkeypatch):
    monkeypatch.setattr(notifier, "PRESUPUESTO_TOTAL", 100)
    inicio = notifier.time.monotonic()
    driver = DriverFalso(listo_tras=None)
    with pytest.raises(notifier.WebNoDisponible):
        notifier.comprobar_radares(driver)
    # Ni una espera supera lo que queda, y el tiempo total no pasa del presupuesto
    # más la última recarga posible (que cargar_pagina limita con TIMEOUT_CARGA_PAGINA)
    assert sum(driver.esperas) <= 100
    assert notifier.time.monotonic() - inicio <= 100 + notifier.TIMEOUT_CARGA_PAGINA
    assert len(espera_falsa) == 1


def test_inicializar_driver_fija_timeout_de_carga(notifier, monkeypatch):
    driver = mock.Mock()
    monkeypatch.setattr(notifier.webdriver, "Chrome", lambda options: driver)
    assert notifier.inicializar_driver() is driver
    driver.set_page_load_timeout.assert_called_once_with(notifier.TIMEOUT_CARGA_PAGINA)


# --- main() ---

def test_main_con_web_no_disponible_no_avisa_a_usuarios(notifier, monkeypatch):
    driver = mock.Mock()
    avisos, mensajes = [], []
    monkeypatch.setattr(notifier, "inicializar_driver", lambda: driver)
    monkeypatch.setattr(notifier, "cargar_pagina", lambda *a, **k: True)

    def comprobar(d):
        notifier.enviar_aviso_admin("aviso de web caída")  # lo que hace esperar_contenido_radares
        raise notifier.WebNoDisponible("simulado")

    monkeypatch.setattr(notifier, "comprobar_radares", comprobar)
    monkeypatch.setattr(notifier, "enviar_aviso_admin", avisos.append)
    monkeypatch.setattr(notifier, "enviar_mensaje_telegram", lambda *a, **k: mensajes.append(a))
    with pytest.raises(notifier.WebNoDisponible):
        notifier.main()
    assert mensajes == []
    assert len(avisos) == 1
    driver.quit.assert_called_once()


def test_main_error_en_quit_no_tapa_la_excepcion_real(notifier, monkeypatch):
    driver = mock.Mock()
    driver.quit.side_effect = RuntimeError("driver muerto")
    monkeypatch.setattr(notifier, "inicializar_driver", lambda: driver)
    monkeypatch.setattr(notifier, "cargar_pagina", lambda *a, **k: True)
    monkeypatch.setattr(notifier, "comprobar_radares", lambda d: (_ for _ in ()).throw(notifier.WebNoDisponible("x")))
    monkeypatch.setattr(notifier, "enviar_aviso_admin", lambda t: None)
    with pytest.raises(notifier.WebNoDisponible):
        notifier.main()


def test_aviso_admin_registra_error_de_telegram(notifier, monkeypatch, caplog):
    monkeypatch.setattr(notifier, "ADMIN_CHAT_ID", "123")
    monkeypatch.setattr(notifier.requests, "post", lambda *a, **k: mock.Mock(status_code=400))
    notifier.enviar_aviso_admin("hola")
    assert "HTTP 400" in caplog.text


# --- Diagnóstico y registro de cada ejecución ---

PANTALLA_ERROR_CHROME = ('<body class="neterror"><div id="main-frame-error">'
                         '<div class="error-code">ERR_TIMED_OUT</div></div></body>')


def sonda_fallida(fase, timeout=True):
    return {"ok": False, "fases_ms": {}, "http_status": None,
            "fallo": {"fase": fase, "tipo": "Error", "timeout": timeout, "mensaje": "x"}}


def test_fallo_con_red_caida_indica_la_causa_en_el_aviso(notifier, espera_falsa, monkeypatch):
    monkeypatch.setattr(notifier.diagnostico_web, "sondear_red", lambda *a, **k: sonda_fallida("tcp"))
    with pytest.raises(notifier.WebNoDisponible) as exc:
        notifier.comprobar_radares(DriverFalso(listo_tras=None))
    assert exc.value.causa == "red_tcp_timeout"
    assert "red_tcp_timeout" in espera_falsa[0] and "falla en TCP (timeout)" in espera_falsa[0]
    assert "log" in espera_falsa[0] and "usuarios" in espera_falsa[0]


def test_pantalla_de_error_de_chrome_con_red_ok_se_distingue(notifier, espera_falsa):
    """Es el caso real del 20-nov: Chrome muestra ERR_TIMED_OUT mientras la sonda sí llega a la web."""
    with pytest.raises(notifier.WebNoDisponible) as exc:
        notifier.comprobar_radares(DriverFalso(listo_tras=None, html=PANTALLA_ERROR_CHROME))
    assert exc.value.causa == "navegador_err_timed_out"
    assert all(i["error_navegador"] == "ERR_TIMED_OUT" for i in notifier._estado["intentos"])
    assert "navegador_err_timed_out" in espera_falsa[0]


def test_texto_distinto_con_red_y_navegador_ok_es_contenido_no_encontrado(notifier, espera_falsa):
    with pytest.raises(notifier.WebNoDisponible) as exc:
        notifier.comprobar_radares(DriverFalso(texto="texto nuevo de la web"))
    assert exc.value.causa == "contenido_no_encontrado"
    contenido = notifier._estado["contenido"]
    assert contenido["inicio"] == "texto nuevo de la web" and contenido["hash"]


def test_se_registra_cada_intento(notifier, espera_falsa):
    notifier.comprobar_radares(DriverFalso(listo_tras=2))
    intentos = notifier._estado["intentos"]
    assert [i["resultado"] for i in intentos] == ["timeout", "timeout", "ok"]
    assert [i["n"] for i in intentos] == [1, 2, 3]
    assert intentos[0]["espera_s"] == 30 and intentos[0]["duracion_s"] == 30


def test_fallo_guarda_evidencias_si_hay_directorio(notifier, espera_falsa, monkeypatch, tmp_path):
    monkeypatch.setattr(notifier, "EVIDENCIAS_DIR", str(tmp_path))
    driver = DriverFalso(listo_tras=None)
    driver.page_source = "<body>https://secreto.invalid/x</body>"
    driver.get_screenshot_as_png = lambda: b"PNG"
    with pytest.raises(notifier.WebNoDisponible):
        notifier.comprobar_radares(driver)
    assert notifier._estado["evidencias"] == ["fallo.html"]  # la captura es opt-in
    assert "secreto.invalid" not in (tmp_path / "fallo.html").read_text()


def test_captura_de_evidencia_solo_si_se_activa(notifier, espera_falsa, monkeypatch, tmp_path):
    monkeypatch.setattr(notifier, "EVIDENCIAS_DIR", str(tmp_path))
    monkeypatch.setattr(notifier, "EVIDENCIAS_CAPTURA", True)
    driver = DriverFalso(listo_tras=None)
    driver.page_source = "<body></body>"
    driver.get_screenshot_as_png = lambda: b"PNG"
    with pytest.raises(notifier.WebNoDisponible):
        notifier.comprobar_radares(driver)
    assert notifier._estado["evidencias"] == ["fallo.html", "fallo.png"]


def test_diagnostico_de_pagina_oculta_ruta_y_query_de_la_url(notifier, espera_falsa, monkeypatch, caplog):
    monkeypatch.setattr(notifier, "donosti_radar_web_url", "https://secreto.invalid/privado/abc?k=1")
    driver = DriverFalso(listo_tras=None, html="<body>/privado/abc?k=1 y https:\\/\\/secreto.invalid</body>")
    with pytest.raises(notifier.WebNoDisponible):
        notifier.comprobar_radares(driver)
    assert "privado" not in caplog.text and "k=1" not in caplog.text


def test_si_el_diagnostico_revienta_el_fallo_sigue_siendo_web_no_disponible(notifier, espera_falsa, monkeypatch):
    def revienta(*a, **k):
        raise RuntimeError("fallo en la sonda")
    monkeypatch.setattr(notifier.diagnostico_web, "sondear_red", revienta)
    with pytest.raises(notifier.WebNoDisponible) as exc:
        notifier.comprobar_radares(DriverFalso(listo_tras=None))
    assert exc.value.causa == "causa_desconocida" and len(espera_falsa) == 1


def preparar_main(notifier, monkeypatch, locations=None, error=None):
    driver = mock.Mock()
    monkeypatch.setattr(notifier, "inicializar_driver", lambda: driver)
    monkeypatch.setattr(notifier, "cargar_pagina", lambda *a, **k: True)
    monkeypatch.setattr(notifier, "enviar_aviso_admin", lambda t: None)

    def comprobar(d):
        if error:
            raise error
        return locations

    monkeypatch.setattr(notifier, "comprobar_radares", comprobar)
    return driver


def test_main_ok_sin_radares_registra_la_ejecucion(notifier, monkeypatch):
    preparar_main(notifier, monkeypatch, locations=[])
    notifier.main()
    docs = list(notifier.collection_ejecuciones.find())
    assert len(docs) == 1
    assert docs[0]["resultado"] == "ok_sin_radares" and docs[0]["causa"] is None
    assert docs[0]["sonda_inicial"]["http_status"] == 200 and docs[0]["duracion_s"] >= 0


def test_main_ok_con_radares_registra_resultado(notifier, monkeypatch):
    preparar_main(notifier, monkeypatch, locations=["Calle A"])
    monkeypatch.setattr(notifier, "obtener_ids_usuarios", lambda: [])
    notifier.main()
    assert notifier.collection_ejecuciones.find_one()["resultado"] == "ok_con_radares"


def test_main_web_no_disponible_registra_la_causa(notifier, monkeypatch):
    preparar_main(notifier, monkeypatch, error=notifier.WebNoDisponible("x", causa="red_dns"))
    with pytest.raises(notifier.WebNoDisponible):
        notifier.main()
    doc = notifier.collection_ejecuciones.find_one()
    assert doc["resultado"] == "fallo" and doc["causa"] == "red_dns"


def test_main_estructura_desconocida_se_registra(notifier, monkeypatch):
    preparar_main(notifier, monkeypatch, locations=None)
    with pytest.raises(SystemExit):
        notifier.main()
    doc = notifier.collection_ejecuciones.find_one()
    assert doc["resultado"] == "fallo" and doc["causa"] == "estructura_desconocida"


def test_main_error_interno_se_registra(notifier, monkeypatch):
    preparar_main(notifier, monkeypatch, error=ValueError("boom"))
    with pytest.raises(ValueError):
        notifier.main()
    doc = notifier.collection_ejecuciones.find_one()
    assert doc["causa"] == "error_interno" and doc["error"] == "ValueError"


def test_main_chrome_no_inicia_avisa_antes_de_registrar(notifier, monkeypatch):
    orden = []
    monkeypatch.setattr(notifier, "inicializar_driver", lambda: None)
    monkeypatch.setattr(notifier, "enviar_aviso_admin", lambda t: orden.append("aviso"))
    monkeypatch.setattr(notifier, "registrar_ejecucion", lambda: orden.append("registro"))
    with pytest.raises(SystemExit):
        notifier.main()
    assert orden == ["aviso", "registro"]


def test_main_chrome_no_inicia_se_registra(notifier, monkeypatch):
    monkeypatch.setattr(notifier, "inicializar_driver", lambda: None)
    monkeypatch.setattr(notifier, "enviar_aviso_admin", lambda t: None)
    with pytest.raises(SystemExit):
        notifier.main()
    assert notifier.collection_ejecuciones.find_one()["causa"] == "chrome_no_inicia"


def test_main_estructura_desconocida_guarda_evidencias_y_sonda(notifier, monkeypatch, tmp_path):
    driver = preparar_main(notifier, monkeypatch, locations=None)
    driver.page_source = "<body>web cambiada</body>"
    monkeypatch.setattr(notifier, "EVIDENCIAS_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        notifier.main()
    doc = notifier.collection_ejecuciones.find_one()
    assert doc["causa"] == "estructura_desconocida" and doc["evidencias"] == ["fallo.html"]
    assert doc["sonda_post_fallo"]["http_status"] == 200
    assert (tmp_path / "fallo.html").read_text() == "<body>web cambiada</body>"


def test_main_error_posterior_al_scraping_conserva_el_resultado(notifier, monkeypatch):
    preparar_main(notifier, monkeypatch, locations=[])
    monkeypatch.setattr(notifier, "obtener_ids_usuarios", lambda: (_ for _ in ()).throw(RuntimeError("mongo")))
    with pytest.raises(RuntimeError):
        notifier.main()
    doc = notifier.collection_ejecuciones.find_one()
    assert doc["resultado"] == "ok_sin_radares" and doc["error"] == "RuntimeError" and doc["causa"] is None


def test_mongo_caido_al_registrar_no_tapa_el_error_real(notifier, monkeypatch, caplog):
    preparar_main(notifier, monkeypatch, error=notifier.WebNoDisponible("x", causa="red_dns"))
    monkeypatch.setattr(notifier.collection_ejecuciones, "insert_one",
                        lambda doc: (_ for _ in ()).throw(RuntimeError("mongo caído")))
    with pytest.raises(notifier.WebNoDisponible):
        notifier.main()
    assert "No se pudo registrar la ejecución" in caplog.text


def test_la_sonda_inicial_no_expone_la_url_en_el_registro(notifier, monkeypatch):
    preparar_main(notifier, monkeypatch, locations=[])
    monkeypatch.setattr(notifier, "donosti_radar_web_url", "https://secreto.invalid/x")
    notifier.main()
    assert "secreto.invalid/x" not in str(notifier.collection_ejecuciones.find_one())


# --- Reintentos diferidos y aviso final a los usuarios ---

def registro_de_hoy(notifier, **campos):
    from datetime import datetime, timezone
    doc = {"momento": datetime.now(timezone.utc).isoformat(), "resultado": "fallo"}
    doc.update(campos)
    notifier.collection_ejecuciones.insert_one(doc)


def registro_de_ayer(notifier, **campos):
    from datetime import datetime, timedelta, timezone
    doc = {"momento": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "resultado": "fallo"}
    doc.update(campos)
    notifier.collection_ejecuciones.insert_one(doc)


def espiar_main(notifier, monkeypatch, driver_ok=True):
    """Sustituye lo que main() usa para salir a la red y anota qué se llama."""
    llamadas = {"driver": 0, "avisos_usuarios": []}
    driver = mock.Mock()

    def init():
        llamadas["driver"] += 1
        return driver if driver_ok else None

    monkeypatch.setattr(notifier, "inicializar_driver", init)
    monkeypatch.setattr(notifier, "cargar_pagina", lambda *a, **k: True)
    monkeypatch.setattr(notifier, "enviar_aviso_admin", lambda t: None)
    monkeypatch.setattr(notifier, "avisar_usuarios_de_fallo",
                        lambda: llamadas["avisos_usuarios"].append(True))
    return llamadas


def test_ya_hecho_hoy_solo_cuenta_scraping_correcto_de_hoy(notifier):
    assert not notifier.ya_hecho_hoy()
    registro_de_hoy(notifier, resultado="fallo")
    registro_de_ayer(notifier, resultado="ok_sin_radares")
    assert not notifier.ya_hecho_hoy()
    registro_de_hoy(notifier, resultado="ok_con_radares")
    assert notifier.ya_hecho_hoy()


def test_reintento_sale_sin_hacer_nada_si_hoy_ya_hubo_exito(notifier, monkeypatch):
    llamadas = espiar_main(notifier, monkeypatch)
    monkeypatch.setattr(notifier, "REINTENTO_DIFERIDO", True)
    registro_de_hoy(notifier, resultado="ok_sin_radares")
    notifier.main()
    assert llamadas["driver"] == 0
    assert notifier.collection_ejecuciones.count_documents({}) == 1  # no añade registro


def test_reintento_se_ejecuta_si_hoy_no_hubo_exito(notifier, monkeypatch):
    espiar_main(notifier, monkeypatch)
    monkeypatch.setattr(notifier, "REINTENTO_DIFERIDO", True)
    monkeypatch.setattr(notifier, "comprobar_radares", lambda d: [])
    registro_de_hoy(notifier, resultado="fallo")          # el intento de la mañana falló
    registro_de_ayer(notifier, resultado="ok_sin_radares")  # el éxito de ayer no cuenta
    notifier.main()
    docs = list(notifier.collection_ejecuciones.find({"modo": "reintento"}))
    assert len(docs) == 1 and docs[0]["resultado"] == "ok_sin_radares"


def test_ejecucion_normal_no_mira_el_historial(notifier, monkeypatch):
    """La de las 05:50 y las manuales siempre se ejecutan, aunque ya hubiera éxito hoy."""
    llamadas = espiar_main(notifier, monkeypatch)
    monkeypatch.setattr(notifier, "comprobar_radares", lambda d: [])
    registro_de_hoy(notifier, resultado="ok_sin_radares")
    notifier.main()
    assert llamadas["driver"] == 1


def test_ultimo_intento_con_fallo_avisa_a_los_usuarios(notifier, monkeypatch):
    llamadas = espiar_main(notifier, monkeypatch)
    monkeypatch.setattr(notifier, "ULTIMO_INTENTO", True)
    monkeypatch.setattr(notifier, "comprobar_radares",
                        lambda d: (_ for _ in ()).throw(notifier.WebNoDisponible("x", causa="red_dns")))
    with pytest.raises(notifier.WebNoDisponible):
        notifier.main()
    assert llamadas["avisos_usuarios"] == [True]


@pytest.mark.parametrize("escenario", ["estructura_desconocida", "chrome_no_inicia", "error_interno"])
def test_ultimo_intento_avisa_en_cualquier_fallo_previo_al_scraping(notifier, monkeypatch, escenario):
    llamadas = espiar_main(notifier, monkeypatch, driver_ok=escenario != "chrome_no_inicia")
    monkeypatch.setattr(notifier, "ULTIMO_INTENTO", True)
    if escenario == "estructura_desconocida":
        monkeypatch.setattr(notifier, "comprobar_radares", lambda d: None)
        esperado = SystemExit
    elif escenario == "error_interno":
        monkeypatch.setattr(notifier, "comprobar_radares", lambda d: (_ for _ in ()).throw(ValueError("x")))
        esperado = ValueError
    else:
        esperado = SystemExit
    with pytest.raises(esperado):
        notifier.main()
    assert llamadas["avisos_usuarios"] == [True]


def test_ultimo_intento_con_exito_no_avisa_de_fallo(notifier, monkeypatch):
    llamadas = espiar_main(notifier, monkeypatch)
    monkeypatch.setattr(notifier, "ULTIMO_INTENTO", True)
    monkeypatch.setattr(notifier, "comprobar_radares", lambda d: [])
    notifier.main()
    assert llamadas["avisos_usuarios"] == []


def test_ultimo_intento_tras_error_posterior_al_scraping_no_avisa_de_fallo(notifier, monkeypatch):
    """Si el scraping fue bien pero falló el envío, no se dice a los usuarios que no se pudo comprobar."""
    llamadas = espiar_main(notifier, monkeypatch)
    monkeypatch.setattr(notifier, "ULTIMO_INTENTO", True)
    monkeypatch.setattr(notifier, "comprobar_radares", lambda d: [])
    monkeypatch.setattr(notifier, "obtener_ids_usuarios", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        notifier.main()
    assert llamadas["avisos_usuarios"] == []


def test_intento_que_no_es_el_ultimo_no_avisa_a_usuarios(notifier, monkeypatch):
    llamadas = espiar_main(notifier, monkeypatch)
    monkeypatch.setattr(notifier, "REINTENTO_DIFERIDO", True)
    monkeypatch.setattr(notifier, "comprobar_radares",
                        lambda d: (_ for _ in ()).throw(notifier.WebNoDisponible("x", causa="red_dns")))
    with pytest.raises(notifier.WebNoDisponible):
        notifier.main()
    assert llamadas["avisos_usuarios"] == []


def sesion_que_anota(codigos, enviados):
    def post(url, data, timeout):
        enviados.append(data)
        return mock.Mock(status_code=codigos.get(data["chat_id"], 200))

    sesion = mock.MagicMock()
    sesion.__enter__.return_value.post = post
    return sesion


def test_aviso_de_fallo_lleva_el_enlace_y_va_a_los_usuarios_activos(notifier, monkeypatch):
    notifier.collection_interactions.insert_many([
        {"chat_id": 1, "active": True}, {"chat_id": 2, "active": False}, {"chat_id": 3, "active": True}])
    enviados = []
    monkeypatch.setattr(notifier.requests, "Session", lambda: sesion_que_anota({}, enviados))
    notifier.avisar_usuarios_de_fallo()
    assert [e["chat_id"] for e in enviados] == [1, 3]
    assert all(notifier.URL_WEB_RADARES in e["text"] and "no he podido comprobar" in e["text"] for e in enviados)
    assert notifier._estado["aviso_usuarios_fallo"] is True


def test_aviso_de_fallo_no_se_repite_el_mismo_dia(notifier, monkeypatch):
    notifier.collection_interactions.insert_one({"chat_id": 1, "active": True})
    registro_de_hoy(notifier, aviso_usuarios_fallo=True)
    enviados = []
    monkeypatch.setattr(notifier.requests, "Session", lambda: sesion_que_anota({}, enviados))
    notifier.avisar_usuarios_de_fallo()
    assert enviados == []


def test_aviso_de_fallo_de_ayer_no_impide_el_de_hoy(notifier, monkeypatch):
    notifier.collection_interactions.insert_one({"chat_id": 1, "active": True})
    registro_de_ayer(notifier, aviso_usuarios_fallo=True)
    enviados = []
    monkeypatch.setattr(notifier.requests, "Session", lambda: sesion_que_anota({}, enviados))
    notifier.avisar_usuarios_de_fallo()
    assert len(enviados) == 1


def test_aviso_de_fallo_marca_inactivo_al_que_bloqueo_el_bot(notifier, monkeypatch):
    notifier.collection_interactions.insert_many([{"chat_id": 1, "active": True}, {"chat_id": 2, "active": True}])
    monkeypatch.setattr(notifier.requests, "Session", lambda: sesion_que_anota({2: 403}, []))
    notifier.avisar_usuarios_de_fallo()
    assert notifier.obtener_ids_usuarios() == [1]


def test_aviso_de_fallo_nunca_lanza(notifier, monkeypatch, caplog):
    monkeypatch.setattr(notifier, "obtener_ids_usuarios", lambda: (_ for _ in ()).throw(RuntimeError("mongo")))
    notifier.avisar_usuarios_de_fallo()
    assert "No se pudo avisar a los usuarios" in caplog.text
    assert not notifier._estado.get("aviso_usuarios_fallo")


def test_el_registro_guarda_el_modo_y_si_se_aviso_a_usuarios(notifier, monkeypatch):
    espiar_main(notifier, monkeypatch)
    monkeypatch.setattr(notifier, "ULTIMO_INTENTO", True)
    monkeypatch.setattr(notifier, "avisar_usuarios_de_fallo",
                        lambda: notifier._estado.__setitem__("aviso_usuarios_fallo", True))
    monkeypatch.setattr(notifier, "comprobar_radares",
                        lambda d: (_ for _ in ()).throw(notifier.WebNoDisponible("x", causa="red_dns")))
    with pytest.raises(notifier.WebNoDisponible):
        notifier.main()
    doc = notifier.collection_ejecuciones.find_one()
    assert doc["modo"] == "ultimo_intento" and doc["aviso_usuarios_fallo"] is True


def test_el_workflow_programa_los_reintentos_y_marca_solo_el_ultimo():
    import os
    import yaml
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".github", "workflows", "mobile_radar_notifier.yml")
    flujo = yaml.safe_load(open(ruta))
    crones = [c["cron"] for c in flujo[True]["schedule"]]  # "on" lo lee YAML como True
    assert crones == ["50 5 * * *", "20 6 * * *", "0 7 * * *", "0 8 * * *"]
    texto = open(ruta).read()
    # La hora del último intento del workflow coincide con el último cron programado
    assert f"github.event.schedule == '{crones[-1]}'" in texto
    assert f"github.event.schedule != '{crones[0]}'" in texto
