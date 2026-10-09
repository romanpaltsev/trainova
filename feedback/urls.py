from django.urls import path

from feedback import views

urlpatterns = [
    path("feedback/", views.FeedbackView.as_view(), name="feedback"),
    path("feedback/<int:pk>/", views.FeedbackDetailView.as_view(), name="feedback_detail"),
]
