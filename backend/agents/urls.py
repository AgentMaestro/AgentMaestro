from django.urls import path

from . import views

app_name = "agents"

urlpatterns = [
    path("new", views.agent_create_wizard, name="agent_create"),
    path("<slug:slug>/", views.agent_detail, name="agent_detail"),
    path("<slug:slug>/telegram-mirror/", views.agent_telegram_mirror_toggle, name="agent_telegram_mirror_toggle"),
    path(
        "<slug:slug>/run/preallocate/",
        views.agent_run_preallocate,
        name="agent_run_preallocate",
    ),
]
