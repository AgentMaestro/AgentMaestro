from django.urls import path

from . import views

app_name = "google_bridge"

urlpatterns = [
    path("agents/<slug:slug>/connect/", views.agent_google_connect, name="agent_connect"),
    path("callback/", views.google_callback, name="callback"),
    path("agents/<slug:slug>/disconnect/", views.agent_google_disconnect, name="agent_disconnect"),
    path("agents/<slug:slug>/status/", views.agent_google_status, name="agent_status"),
]
