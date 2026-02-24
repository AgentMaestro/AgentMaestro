from django.urls import re_path

from . import consumers


websocket_urlpatterns = [
    # Workspace-wide stream (dashboard + approvals)
    re_path(r"^ws/ui/workspace/$", consumers.WorkspaceConsumer.as_asgi()),
    # Per-run stream (run detail page)
    re_path(r"^ws/ui/run/(?P<run_id>[0-9a-fA-F-]+)/$", consumers.RunConsumer.as_asgi()),
]
