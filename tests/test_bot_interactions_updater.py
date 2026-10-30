from unittest import mock

UPDATES = [
    {"update_id": 10, "message": {"message_id": 1, "date": 1700000000, "chat": {"id": 5, "type": "private"},
                                  "text": "/start", "entities": [{"type": "bot_command", "offset": 0, "length": 6}]}},
    {"update_id": 11, "message": {"message_id": 2, "date": 1700000001, "chat": {"id": 5, "type": "private"}, "sticker": {}}},
    {"update_id": 12, "message": {"message_id": 3, "date": 1700000002, "chat": {"id": 6, "type": "private"}, "text": "hola"}},
]


def respuesta(result):
    r = mock.Mock()
    r.json.return_value = {"ok": True, "result": result}
    return r


def test_mensajes_sin_texto_no_rompen(updater):
    chats = updater.transformar_a_estructura_mongo(UPDATES)
    assert [c["chat_id"] for c in chats] == [5, 6]
    assert chats[0]["messages"][1]["text"] == ""
    assert chats[0]["messages"][0]["command"] == "bot_command"


def test_offset_evita_reprocesar(updater):
    llamadas = []

    def fake_get(url, params=None, timeout=None):
        llamadas.append(params)
        return respuesta(UPDATES)

    with mock.patch.object(updater.requests, "get", fake_get):
        updater.main()
        updater.main()

    assert llamadas == [{}, {"offset": 13}]
    assert updater.collection.count_documents({}) == 2
    # Los mensajes no se duplican aunque Telegram los devuelva otra vez
    assert len(updater.collection.find_one({"chat_id": 5})["messages"]) == 2


def test_usuario_que_vuelve_a_escribir_se_reactiva(updater):
    updater.collection.insert_one({"chat_id": 6, "active": False, "messages": []})
    with mock.patch.object(updater.requests, "get", return_value=respuesta(UPDATES)):
        updater.main()
    assert updater.collection.find_one({"chat_id": 6})["active"] is True
