from django.urls import path

from adminpanel import views

# Все имена начинаются с admin_: по префиксу горит пункт «Админка» в панели.
urlpatterns = [
    path("", views.AdminHomeView.as_view(), name="admin_home"),
    path("users/", views.AdminUsersView.as_view(), name="admin_users"),
    path("system/", views.AdminSystemView.as_view(), name="admin_system"),
]
