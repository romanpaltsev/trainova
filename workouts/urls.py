from django.urls import path

from workouts import views

urlpatterns = [
    path("history/", views.WorkoutHistoryView.as_view(), name="workout_history"),
    path("workouts/cardio/new/", views.CardioWorkoutFormView.as_view(), name="cardio_create"),
    path("workouts/<int:pk>/edit/", views.CardioWorkoutFormView.as_view(), name="workout_edit"),
    path("workouts/<int:pk>/delete/", views.WorkoutDeleteView.as_view(), name="workout_delete"),
    path("sports/new/", views.SportCreateView.as_view(), name="sport_create"),
]
