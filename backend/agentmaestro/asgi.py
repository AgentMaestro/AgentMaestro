import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

# Ensure the settings module is set (compose can override via env)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "agentmaestro.settings.dev")

django_asgi_app = get_asgi_application()

# Import websocket routes (kept inside ui app)
import ui.routing  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(
            URLRouter(ui.routing.websocket_urlpatterns),
        ),
    }
)
