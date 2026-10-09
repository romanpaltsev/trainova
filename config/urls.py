from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from config import views

urlpatterns = [
    # Свой раздел «Админка» — на /admin/, встроенная админка Django — рядом.
    path("django-admin/", admin.site.urls),
    path("admin/", include("adminpanel.urls")),
    path("accounts/", include("allauth.urls")),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("favicon.ico", views.favicon),
    # iOS просит эти пути у корня, если не смог воспользоваться <link>.
    path("apple-touch-icon.png", views.apple_touch_icon),
    path("apple-touch-icon-precomposed.png", views.apple_touch_icon),
    # Префикс accounts/ принадлежит allauth, поэтому свои экраны аккаунта
    # живут в корне — как дашборд.
    path("", include("accounts.urls")),
    # Дашборд (name="dashboard") живёт в workouts: это витрина тренировок.
    path("", include("workouts.urls")),
]

if settings.DEBUG_TOOLBAR:
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns += debug_toolbar_urls()
