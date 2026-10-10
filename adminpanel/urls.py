from django.urls import path

from adminpanel import views

# Все имена начинаются с admin_: по префиксу горит пункт «Админка» в панели.
urlpatterns = [
    path("", views.AdminHomeView.as_view(), name="admin_home"),
    path("users/", views.AdminUsersView.as_view(), name="admin_users"),
    path("system/", views.AdminSystemView.as_view(), name="admin_system"),
    path("feedback/", views.AdminFeedbackListView.as_view(), name="admin_feedback"),
    path(
        "feedback/<int:pk>/",
        views.AdminFeedbackDetailView.as_view(),
        name="admin_feedback_detail",
    ),
    path("requests/", views.AdminRequestsView.as_view(), name="admin_requests"),
    path(
        "requests/<int:pk>/",
        views.AdminRequestDetailView.as_view(),
        name="admin_request_detail",
    ),
]
