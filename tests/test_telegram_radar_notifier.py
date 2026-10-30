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
