from django.urls import path

from . import views

app_name = "agents"

urlpatterns = [
    path("new", views.agent_create_wizard, name="agent_create"),
    path("<slug:slug>/", views.agent_detail, name="agent_detail"),
    path("<slug:slug>/artifacts/upload/", views.agent_artifact_upload, name="agent_artifact_upload"),
    path("<slug:slug>/artifacts/google-drive-import/", views.agent_google_drive_import, name="agent_google_drive_import"),
    path(
        "<slug:slug>/artifacts/<uuid:artifact_id>/delete/",
        views.agent_artifact_delete,
        name="agent_artifact_delete",
    ),
    path(
        "<slug:slug>/artifacts/<uuid:artifact_id>/download/",
        views.agent_artifact_download,
        name="agent_artifact_download",
    ),
    path("<slug:slug>/telegram-mirror/", views.agent_telegram_mirror_toggle, name="agent_telegram_mirror_toggle"),
    path(
        "<slug:slug>/run/preallocate/",
        views.agent_run_preallocate,
        name="agent_run_preallocate",
    ),
]
