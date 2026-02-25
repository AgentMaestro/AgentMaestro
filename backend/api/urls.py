from django.urls import path

from . import views

app_name = 'api'

urlpatterns = [
    path('runs/', views.start_run, name='start_run'),
    path('runs/<uuid:run_id>/spawn_subrun/', views.spawn_subrun_view, name='spawn_subrun'),
    path('toolcalls/<uuid:tool_call_id>/approve/', views.approve_tool_call_view, name='approve_tool_call'),
    path('runs/<uuid:run_id>/snapshot/', views.run_snapshot_view, name='run_snapshot'),
]
