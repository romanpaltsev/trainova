from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    # Дашборд (name="dashboard") живёт в workouts: это витрина тренировок.
    path("", include("workouts.urls")),
]
