# backend/agentmaestro/urls.py
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Django Admin
    path("admin/", admin.site.urls),

    path("agents/", include(("agents.urls", "agents"), namespace="agents")),

    path("control/", include(("control.urls", "control"), namespace="control")),

    path("comms/", include(("comms.urls", "comms"), namespace="comms")),

    path("llm/", include(("llm.urls", "llm"), namespace="llm")),

    path("google/", include(("google_bridge.urls", "google_bridge"), namespace="google_bridge")),

    # UI app
    path("ui/", include(("ui.urls", "ui"), namespace="ui")),

    path("api/", include(("api.urls", "api"), namespace="api")),
]


admin.site.site_header = "AgentMaestro Control Panel"
admin.site.site_title = "AgentMaestro Admin"
admin.site.index_title = "Orchestration Management"
