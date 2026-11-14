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
    """Driver simulado: 'span12' aparece a partir del intento `exito_en` (1-based; None = nunca)."""

    title = "Titulo de prueba"

    def __init__(self, exito_en=1, texto="No hay ninguna ubicación planificada para hoy.",
                 html="<body>https://secreto.invalid/x " + "a" * 5000 + "</body>"):
        self.exito_en, self.texto, self.html = exito_en, texto, html
        self.esperas = []   # timeouts pedidos a WebDriverWait
        self.cargas = 0     # llamadas a driver.get

    def get(self, url):
        self.cargas += 1

    def find_elements(self, by, valor):
        return [ElementoFalso([self.texto])] if valor == "span12" else []

    def find_element(self, by, valor):
        return ElementoFalso(html=self.html)


@pytest.fixture
def espera_falsa(notifier, monkeypatch):
    """Sustituye WebDriverWait y time.sleep: no hay esperas ni red reales."""
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)

    class EsperaFalsa:
        def __init__(self, driver, timeout):
            self.driver, self.timeout = driver, timeout
            driver.esperas.append(timeout)

        def until(self, condicion):
            d = self.driver
            if d.exito_en is not None and len(d.esperas) >= d.exito_en:
                return True
            raise TimeoutException("simulado")

    monkeypatch.setattr(notifier, "WebDriverWait", EsperaFalsa)
    monkeypatch.setattr(notifier, "donosti_radar_web_url", "https://secreto.invalid/x")
    avisos = []
    monkeypatch.setattr(notifier, "enviar_aviso_admin", avisos.append)
    return avisos


def test_exito_al_primer_intento(notifier, espera_falsa):
    driver = DriverFalso(exito_en=1)
    assert notifier.comprobar_radares(driver) == []
    assert driver.esperas[0] == 30
    assert driver.cargas == 0
    assert espera_falsa == []


def test_exito_tras_reintento_recarga_con_espera_mayor(notifier, espera_falsa):
    driver = DriverFalso(exito_en=3)
    assert notifier.comprobar_radares(driver) == []
    assert driver.esperas[:3] == [30, 60, 60]
    assert driver.cargas == 2  # se recargó la página antes de cada reintento
    assert espera_falsa == []


def test_fallo_definitivo_avisa_al_admin_y_no_a_usuarios(notifier, espera_falsa, monkeypatch, caplog):
    monkeypatch.setattr(notifier, "enviar_mensaje_telegram",
                        lambda *a, **k: pytest.fail("no debe avisar a los usuarios"))
    driver = DriverFalso(exito_en=None)
    with pytest.raises(notifier.WebNoDisponible):
        notifier.comprobar_radares(driver)
    assert driver.esperas == [30, 60, 60, 60]
    assert driver.cargas == 3
    assert len(espera_falsa) == 1 and "log" in espera_falsa[0]
    registro = caplog.text
    assert "Titulo de prueba" in registro
    assert "secreto.invalid" not in registro      # la URL no se vuelca
    assert "a" * 2900 in registro and "a" * 3000 not in registro  # HTML acotado


def test_estado_desconocido_devuelve_none_tras_reintento(notifier, espera_falsa):
    driver = DriverFalso(exito_en=2, texto="no hay nada relevante")
    assert notifier.comprobar_radares(driver) is None
