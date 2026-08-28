from django.urls import path

from workouts import views

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("dashboard/week/", views.DashboardWeekView.as_view(), name="dashboard_week"),
    path("exercises/", views.ExerciseListView.as_view(), name="exercise_list"),
    path("exercises/<int:pk>/", views.ExerciseDetailView.as_view(), name="exercise_detail"),
    path("exercises/<int:pk>/delete/", views.ExerciseDeleteView.as_view(), name="exercise_delete"),
    path("profile/sports/", views.MySportsView.as_view(), name="my_sports"),
    path("profile/sports/<int:pk>/delete/", views.SportDeleteView.as_view(), name="sport_delete"),
    path("changelog/", views.ChangelogView.as_view(), name="changelog"),
    path("history/", views.WorkoutHistoryView.as_view(), name="workout_history"),
    path("workouts/cardio/new/", views.CardioWorkoutFormView.as_view(), name="cardio_create"),
    path("workouts/<int:pk>/edit/", views.CardioWorkoutFormView.as_view(), name="workout_edit"),
    path("workouts/<int:pk>/delete/", views.WorkoutDeleteView.as_view(), name="workout_delete"),
    path("sports/new/", views.SportCreateView.as_view(), name="sport_create"),
    # Живой режим силовой тренировки
    path("workouts/start/", views.WorkoutStartView.as_view(), name="workout_start"),
    path(
        "workouts/strength/start/",
        views.StrengthWorkoutStartView.as_view(),
        name="strength_start",
    ),
    path("workouts/<int:pk>/live/", views.LiveWorkoutView.as_view(), name="workout_live"),
    path(
        "workouts/<int:pk>/live/exercises/",
        views.LiveExerciseView.as_view(),
        name="live_exercises",
    ),
    path(
        "workouts/<int:pk>/live/select/",
        views.LiveExerciseSelectView.as_view(),
        name="live_exercise_select",
    ),
    path("workouts/<int:pk>/live/sets/", views.LiveSetAddView.as_view(), name="live_set_add"),
    path("workouts/<int:pk>/live/rest/", views.LiveRestView.as_view(), name="live_rest"),
    path("workouts/<int:pk>/finish/", views.WorkoutFinishView.as_view(), name="workout_finish"),
    path("workouts/<int:pk>/summary/", views.WorkoutSummaryView.as_view(), name="workout_summary"),
    path("workouts/<int:pk>/repeat/", views.WorkoutRepeatView.as_view(), name="workout_repeat"),
    path("sets/<int:pk>/adjust/", views.SetAdjustView.as_view(), name="set_adjust"),
    path("sets/<int:pk>/done/", views.SetDoneView.as_view(), name="set_done"),
    path("sets/<int:pk>/undo/", views.SetUndoView.as_view(), name="set_undo"),
    path("sets/<int:pk>/delete/", views.SetDeleteView.as_view(), name="set_delete"),
]
