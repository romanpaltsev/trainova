"""Границы числа запросов: число не должно расти вместе с данными."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.tests.factories import (
    CardioDetailsFactory,
    ExerciseFactory,
    ExerciseNoteFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def fill_history(user, weeks=6):
    """Немного истории: силовые с подходами и кардио — как у живого пользователя."""
    bench = ExerciseFactory(name="Жим лёжа")
    squat = ExerciseFactory(name="Присед со штангой")
    for week in range(weeks):
        started = timezone.now() - timedelta(weeks=week, days=1)
        workout = WorkoutFactory(user=user, started_at=started)
        for number, exercise in enumerate((bench, squat), start=1):
            StrengthSetFactory(
                workout=workout, exercise=exercise, set_number=number, weight_kg=70 + week, reps=8
            )
        CardioDetailsFactory(
            workout__user=user, workout__started_at=started - timedelta(days=2), distance_km=10
        )
    return bench


def test_dashboard_query_budget(client, user, django_assert_max_num_queries):
    """Дашборд собирает сводку, график, рекорды и последние тренировки."""
    fill_history(user)

    client.force_login(user)
    with django_assert_max_num_queries(15):
        client.get(reverse("dashboard"))


def test_dashboard_queries_do_not_scale_with_history(client, user, django_assert_max_num_queries):
    fill_history(user, weeks=12)

    client.force_login(user)
    with django_assert_max_num_queries(15):
        client.get(reverse("dashboard"))


def test_exercise_page_query_budget(client, user, django_assert_max_num_queries):
    bench = fill_history(user)

    client.force_login(user)
    # Седьмой запрос — заметки упражнения (один на всю историю), восьмой — список
    # групп мышц для чипов, девятый — позиции упражнения в тренировках («каким
    # по счёту делал»). Ни один не зависит от объёма истории.
    with django_assert_max_num_queries(9):
        client.get(reverse("exercise_detail", args=[bench.pk]))


def test_exercise_page_queries_do_not_scale_with_history(
    client, user, django_assert_max_num_queries
):
    """Позиции считаются одним агрегатом на всю историю, а не по тренировке."""
    bench = fill_history(user, weeks=12)

    client.force_login(user)
    with django_assert_max_num_queries(9):
        client.get(reverse("exercise_detail", args=[bench.pk]))


def test_exercise_panel_query_budget(client, user, django_assert_max_num_queries):
    """Панель мастер-детали — та же вьюха: партиал не должен добавлять запросов."""
    bench = fill_history(user)

    client.force_login(user)
    with django_assert_max_num_queries(9):
        client.get(reverse("exercise_detail", args=[bench.pk]), headers={"HX-Request": "true"})


def test_catalog_query_budget(client, user, django_assert_max_num_queries):
    fill_history(user)

    client.force_login(user)
    with django_assert_max_num_queries(7):
        client.get(reverse("exercise_list"))


@pytest.mark.parametrize("queued", [1, 6], ids=["one-exercise", "six-exercises"])
def test_live_screen_query_budget(client, user, django_assert_max_num_queries, queued):
    """Живой экран не должен зависеть от числа упражнений в очереди."""
    fill_history(user)
    active = WorkoutFactory(user=user, duration_min=None)
    for number, exercise in enumerate(ExerciseFactory.create_batch(queued), start=1):
        StrengthSetFactory(workout=active, exercise=exercise, set_number=number, done=False)

    client.force_login(user)
    with django_assert_max_num_queries(8):
        client.get(reverse("workout_live", args=[active.pk]))


@pytest.mark.parametrize("queued", [1, 6], ids=["one-exercise", "six-exercises"])
def test_live_screen_with_notes_query_budget(client, user, django_assert_max_num_queries, queued):
    """Заметки берутся одним запросом на тренировку плюс одним на прошлую заметку
    текущего упражнения — и это не зависит от числа упражнений в очереди."""
    bench = fill_history(user)
    active = WorkoutFactory(user=user, duration_min=None)
    # Текущее упражнение с историей: только тогда считается прошлая заметка.
    StrengthSetFactory(workout=active, exercise=bench, set_number=1, done=False)
    ExerciseNoteFactory(workout=active, exercise=bench, text="узкий хват")
    for number, exercise in enumerate(ExerciseFactory.create_batch(queued), start=2):
        StrengthSetFactory(workout=active, exercise=exercise, set_number=number, done=False)
        ExerciseNoteFactory(workout=active, exercise=exercise, text="заметка очереди")

    client.force_login(user)
    with django_assert_max_num_queries(10):
        client.get(reverse("workout_live", args=[active.pk]))


@pytest.mark.parametrize("queued", [1, 6], ids=["one-exercise", "six-exercises"])
def test_draft_screen_query_budget(client, user, django_assert_max_num_queries, queued):
    """Экран черновика — тот же живой экран без таймера: бюджет не выше."""
    fill_history(user)
    planned = WorkoutFactory(user=user, started_at=None, duration_min=None)
    for number, exercise in enumerate(ExerciseFactory.create_batch(queued), start=1):
        StrengthSetFactory(workout=planned, exercise=exercise, set_number=number, done=False)

    client.force_login(user)
    with django_assert_max_num_queries(8):
        client.get(reverse("workout_live", args=[planned.pk]))


@pytest.mark.parametrize("drafts", [1, 4], ids=["one-draft", "four-drafts"])
def test_start_modal_query_budget(client, user, django_assert_max_num_queries, drafts):
    """Число запросов чузера не должно расти вместе с числом черновиков."""
    fill_history(user)
    for _ in range(drafts):
        planned = WorkoutFactory(user=user, started_at=None, duration_min=None)
        StrengthSetFactory(workout=planned, set_number=1, done=False)

    client.force_login(user)
    # Идущая тренировка и черновики берутся одним запросом с аннотацией.
    with django_assert_max_num_queries(6):
        client.get(reverse("workout_start"))


def test_profile_query_budget(client, user, django_assert_max_num_queries):
    fill_history(user)

    client.force_login(user)
    with django_assert_max_num_queries(9):
        client.get(reverse("profile"))


@pytest.mark.parametrize("exercises", [1, 6], ids=["one-exercise", "six-exercises"])
def test_workout_summary_query_budget(client, user, django_assert_max_num_queries, exercises):
    """Итог тренировки не должен зависеть от числа упражнений в ней.

    Экран показывает номер, объём и подходы каждого упражнения — то есть ровно тот
    сорт данных, куда легко въезжает запрос на упражнение. Ценность теста не в
    самом числе, а в том, что оно одинаково при одном упражнении и при шести.
    """
    workout = WorkoutFactory(user=user, duration_min=60)
    for number in range(exercises):
        exercise = ExerciseFactory(name=f"Упражнение {number}")
        StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, weight_kg=70, reps=8)

    client.force_login(user)
    with django_assert_max_num_queries(7):
        client.get(reverse("workout_summary", args=[workout.pk]))
