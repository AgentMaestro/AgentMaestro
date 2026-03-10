# backend/conftest.py
import pytest


@pytest.fixture(autouse=True)
def _configure_channel_layer(settings):
    """
    Default: RedisChannelLayer (matches production settings) is always used; no toggle needed.
    """
    settings.TESTING = True
    return settings
