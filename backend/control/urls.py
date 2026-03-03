from django.urls import path

from . import views

app_name = "control"

urlpatterns = [
    path("", views.chat_home, name="chat_home"),
    path("<uuid:uuid>/", views.chat_detail, name="chat_detail"),
    path("<uuid:uuid>/send/", views.chat_send, name="chat_send"),
]
