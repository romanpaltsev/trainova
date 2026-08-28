from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from config import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("favicon.ico", views.favicon),
    # Префикс accounts/ принадлежит allauth, поэтому свои экраны аккаунта
    # живут в корне — как дашборд.
    path("", include("accounts.urls")),
    # Дашборд (name="dashboard") живёт в workouts: это витрина тренировок.
    path("", include("workouts.urls")),
]

if settings.DEBUG_TOOLBAR:
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns += debug_toolbar_urls()
