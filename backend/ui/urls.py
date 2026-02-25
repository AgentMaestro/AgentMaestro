from django.urls import path

from . import views

urlpatterns = [
    path("dev/ws/", views.dev_ws_test, name="dev_ws_test"),
    path("dev/start-run/", views.dev_start_run, name="dev_start_run"),
    path("run/<uuid:run_id>/", views.run_detail, name="run_detail"),
    path("run/<uuid:run_id>/snapshot/", views.run_snapshot, name="run_snapshot"),
    path(
        "run/<uuid:run_id>/archive/<uuid:archive_id>/download/",
        views.download_run_archive,
        name="run_archive_download",
    ),
]
