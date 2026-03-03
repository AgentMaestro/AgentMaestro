from django.urls import re_path

from . import consumers
from agents.consumers import AgentChatConsumer
from control.consumers import ControlChatConsumer


websocket_urlpatterns = [
    # Workspace-wide stream (dashboard + approvals)
    re_path(r"^ws/ui/workspace/$", consumers.WorkspaceConsumer.as_asgi()),
    # Control chat stream
    re_path(r"^ws/ui/chat/(?P<uuid>[0-9a-fA-F-]+)/$", ControlChatConsumer.as_asgi()),
    # Per-run stream (run detail page)
    re_path(r"^ws/ui/run/(?P<run_id>[0-9a-fA-F-]+)/$", consumers.RunConsumer.as_asgi()),
    # Agent detail chat (allows optional leading slash)
    re_path(
        r"^/?ws/agents/(?P<slug>[-\\w]+)/chat/$",
        AgentChatConsumer.as_asgi(),
    ),
]
