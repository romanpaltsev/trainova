from django.contrib import admin
from django.urls import include, path

from accounts.views import DashboardView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", DashboardView.as_view(), name="dashboard"),
    path("", include("workouts.urls")),
]
