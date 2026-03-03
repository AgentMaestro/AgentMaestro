from django.urls import include, path

urlpatterns = [
    path("console/", include(("llm.urls_console", "console"), namespace="console")),
]
