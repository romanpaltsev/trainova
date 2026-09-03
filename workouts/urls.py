from django.urls import path

from workouts import views

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("dashboard/week/", views.DashboardWeekView.as_view(), name="dashboard_week"),
    path("exercises/", views.ExerciseListView.as_view(), name="exercise_list"),
    path("exercises/<int:pk>/", views.ExerciseDetailView.as_view(), name="exercise_detail"),
    path("exercises/<int:pk>/delete/", views.ExerciseDeleteView.as_view(), name="exercise_delete"),
    path(
        "exercises/<int:pk>/measurement/",
        views.ExerciseMeasurementView.as_view(),
        name="exercise_measurement",
    ),
    path(
        "exercises/<int:pk>/weight-step/",
        views.ExerciseWeightStepView.as_view(),
        name="exercise_weight_step",
    ),
    path(
        "exercises/<int:pk>/muscle-group/",
        views.ExerciseMuscleGroupView.as_view(),
        name="exercise_muscle_group",
    ),
    path("profile/sports/", views.MySportsView.as_view(), name="my_sports"),
    path("profile/sports/<int:pk>/delete/", views.SportDeleteView.as_view(), name="sport_delete"),
    path("profile/locations/", views.MyLocationsView.as_view(), name="my_locations"),
    path(
        "profile/locations/<int:pk>/default/",
        views.LocationDefaultView.as_view(),
        name="location_default",
    ),
    path(
        "profile/locations/<int:pk>/rename/",
        views.LocationRenameView.as_view(),
        name="location_rename",
    ),
    path(
        "profile/locations/<int:pk>/delete/",
        views.LocationDeleteView.as_view(),
        name="location_delete",
    ),
    path("changelog/", views.ChangelogView.as_view(), name="changelog"),
    path("history/", views.WorkoutHistoryView.as_view(), name="workout_history"),
    path("workouts/cardio/new/", views.CardioWorkoutFormView.as_view(), name="cardio_create"),
    # Подготовка кардио заранее: та же вьюха и та же форма без даты, длительности
    # и пульса. Отдельный маршрут, а не флаг в query, — чтобы «записать» и
    # «запланировать» нельзя было перепутать подменой параметра.
    path(
        "workouts/cardio/prepare/",
        views.CardioWorkoutFormView.as_view(planned=True),
        name="cardio_prepare",
    ),
    path("workouts/<int:pk>/edit/", views.CardioWorkoutFormView.as_view(), name="workout_edit"),
    path("workouts/<int:pk>/delete/", views.WorkoutDeleteView.as_view(), name="workout_delete"),
    # День черновика. Отдельный эндпоинт, а не поле в форме: силовой черновик
    # создаётся одним тапом из чузера, формы у него нет вовсе.
    path(
        "workouts/<int:pk>/planned-for/",
        views.WorkoutPlannedForView.as_view(),
        name="workout_planned_for",
    ),
    path("sports/new/", views.SportCreateView.as_view(), name="sport_create"),
    # Живой режим силовой тренировки
    path("workouts/start/", views.WorkoutStartView.as_view(), name="workout_start"),
    path(
        "workouts/strength/start/",
        views.StrengthWorkoutStartView.as_view(),
        name="strength_start",
    ),
    path(
        "workouts/strength/prepare/",
        views.StrengthWorkoutStartView.as_view(planned=True),
        name="strength_prepare",
    ),
    # Черновик и идущая тренировка живут на одном URL: после старта адрес не
    # меняется, открытая вкладка и закладка остаются рабочими.
    path("workouts/<int:pk>/live/", views.LiveWorkoutView.as_view(), name="workout_live"),
    path("workouts/<int:pk>/start/", views.WorkoutDraftStartView.as_view(), name="draft_start"),
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
    # Упражнение — параметром, как у live_set_add и live_exercise_select: тогда
    # маршрут добавляется в общие таблицы тестов изоляции с одним args=[pk].
    path("workouts/<int:pk>/live/note/", views.ExerciseNoteView.as_view(), name="live_note"),
    path("workouts/<int:pk>/live/rest/", views.LiveRestView.as_view(), name="live_rest"),
    # Не под live/: место правится и у записанной тренировки — экрана правки
    # силовой нет, и иначе забытое место осталось бы неисправимым.
    path(
        "workouts/<int:pk>/location/",
        views.WorkoutLocationView.as_view(),
        name="workout_location",
    ),
    path("workouts/<int:pk>/finish/", views.WorkoutFinishView.as_view(), name="workout_finish"),
    path("workouts/<int:pk>/summary/", views.WorkoutSummaryView.as_view(), name="workout_summary"),
    path("workouts/<int:pk>/repeat/", views.WorkoutRepeatView.as_view(), name="workout_repeat"),
    path("sets/<int:pk>/adjust/", views.SetAdjustView.as_view(), name="set_adjust"),
    path("sets/<int:pk>/value/", views.SetValueView.as_view(), name="set_value"),
    path("sets/<int:pk>/done/", views.SetDoneView.as_view(), name="set_done"),
    path("sets/<int:pk>/undo/", views.SetUndoView.as_view(), name="set_undo"),
    path("sets/<int:pk>/delete/", views.SetDeleteView.as_view(), name="set_delete"),
]
