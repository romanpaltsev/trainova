from django.urls import path

from feedback import views

urlpatterns = [
    path("feedback/", views.FeedbackView.as_view(), name="feedback"),
]
