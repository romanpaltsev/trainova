from django.urls import path

from accounts import views

urlpatterns = [
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("profile/rest/", views.ProfileRestView.as_view(), name="profile_rest"),
]
