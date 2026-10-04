from django.urls import path

from accounts import views

urlpatterns = [
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("profile/rest/", views.ProfileRestView.as_view(), name="profile_rest"),
    path("profile/goal/", views.ProfileGoalView.as_view(), name="profile_goal"),
    # Имя с account_ — так панель ПК подсвечивает «Профиль», как у страниц allauth.
    path("profile/delete/", views.AccountDeletionView.as_view(), name="account_deletion"),
]
