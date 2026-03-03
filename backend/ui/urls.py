from django.urls import include, path

from . import views
from control import views as control_views

urlpatterns = [
    path("dev/ws/", views.dev_ws_test, name="dev_ws_test"),
    path("dev/login/test-user/", views.dev_login_test_user, name="dev_login_test_user"),
    path("dev/start-run/", views.dev_start_run, name="dev_start_run"),
    path("run/<uuid:run_id>/", views.run_detail, name="run_detail"),
    path("run/<uuid:run_id>/snapshot/", views.run_snapshot, name="run_snapshot"),
    path(
        "run/<uuid:run_id>/archive/<uuid:archive_id>/download/",
        views.download_run_archive,
        name="run_archive_download",
    ),
    path("chat/", include(("control.urls", "control"), namespace="control")),
    path(
        "agents/<uuid:agent_uuid>/connect/telegram/",
        control_views.connect_telegram,
        name="connect_telegram",
    ),
    path(
        "pairings/<uuid:pairing_uuid>/status/",
        control_views.pairing_status,
        name="pairing_status",
    ),
    path("comms/telegram/", control_views.telegram_chat, name="telegram_chat"),
]
