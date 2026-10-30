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
    import telegram_radar_notifier


@pytest.fixture
def updater():
    bot_interactions_updater.collection.delete_many({})
    bot_interactions_updater.collection_state.delete_many({})
    return bot_interactions_updater


@pytest.fixture
def notifier():
    telegram_radar_notifier.collection_interactions.delete_many({})
    return telegram_radar_notifier
