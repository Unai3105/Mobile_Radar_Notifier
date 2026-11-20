import os
import sys
from unittest import mock

import mongomock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.update(
    MONGO_URI="mongodb://localhost",
    MONGO_DB="test",
    MONGO_COLLECTION_INTERACTIONS="interactions",
    MONGO_COLLECTION_REPORTS="reports",
    TELEGRAM_TOKEN="token",
)

# Los scripts se conectan a MongoDB al importarse: se sustituye por mongomock
with mock.patch("pymongo.MongoClient", mongomock.MongoClient):
    import bot_interactions_updater
    import diagnostico_web
    import telegram_radar_notifier


@pytest.fixture
def updater():
    bot_interactions_updater.collection.delete_many({})
    bot_interactions_updater.collection_state.delete_many({})
    return bot_interactions_updater


SONDA_OK = {
    "momento": "2026-01-01T00:00:00+00:00", "host": "web.invalid", "ip_runner": "1.2.3.4",
    "ip_destino": "5.6.7.8", "fases_ms": {"dns": 10, "tcp": 20, "tls": 30, "http": 100},
    "ok": True, "fallo": None, "http_status": 200, "bytes": 1000, "textos_en_html": None,
}


@pytest.fixture(autouse=True)
def sin_red_real(monkeypatch):
    """Ningún test sale a la red: la sonda devuelve siempre un resultado correcto."""
    monkeypatch.setattr(diagnostico_web, "sondear_red", lambda *a, **k: dict(SONDA_OK))


@pytest.fixture
def notifier():
    telegram_radar_notifier.collection_interactions.delete_many({})
    telegram_radar_notifier.collection_ejecuciones.delete_many({})
    telegram_radar_notifier._reiniciar_estado()
    return telegram_radar_notifier
