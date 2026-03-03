from django.urls import path

from .views_console import console_detail, console_stream

app_name = "console"

urlpatterns = [
    path("stream", console_stream, name="console_stream"),
    path("detail", console_detail, name="console_detail"),
]
