# backend/conftest.py
import os
import pytest


@pytest.fixture(autouse=True)
def _configure_channel_layer(settings):
    """
    Default: InMemoryChannelLayer (fast, no infra required)
    If USE_REDIS_CHANNEL_LAYER=1 is set, use Redis for Channels.
    """
    use_redis = os.getenv("USE_REDIS_CHANNEL_LAYER") == "1"
    if use_redis:
        redis_url = os.getenv("CHANNEL_LAYER_REDIS_URL", "redis://127.0.0.1:6379/1")
        settings.CHANNEL_LAYERS = {
            "default": {
                "BACKEND": "channels_redis.core.RedisChannelLayer",
                "CONFIG": {"hosts": [redis_url]},
            }
        }
    else:
        settings.CHANNEL_LAYERS = {
            "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
        }
    return settings