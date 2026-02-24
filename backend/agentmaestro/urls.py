# backend/agentmaestro/urls.py
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Django Admin
    path("admin/", admin.site.urls),

    # UI app
    path("ui/", include(("ui.urls", "ui"), namespace="ui")),

    # Future:
    # path("api/", include("api.urls")),
]


admin.site.site_header = "AgentMaestro Control Panel"
admin.site.site_title = "AgentMaestro Admin"
admin.site.index_title = "Orchestration Management"
