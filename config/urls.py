from django.urls import include, path
from dashboard.views.health import health_check

urlpatterns = [
    path("health", health_check, name="health_check"),
    path("", include("dashboard.urls")),
]
