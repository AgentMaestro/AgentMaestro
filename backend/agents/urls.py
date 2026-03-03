from django.urls import path

from . import views

app_name = "agents"

urlpatterns = [
    path("new", views.agent_create_wizard, name="agent_create"),
    path("<slug:slug>/", views.agent_detail, name="agent_detail"),
]
