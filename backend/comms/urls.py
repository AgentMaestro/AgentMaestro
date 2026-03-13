from django.urls import path

from . import views

app_name = "comms"

urlpatterns = [
    path("telegram/<int:endpoint_id>/webhook/", views.telegram_webhook, name="telegram_webhook"),
]
