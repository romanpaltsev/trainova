"""Границы числа запросов: число не должно расти вместе с данными."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.tests.factories import (
    CardioDetailsFactory,
    ExerciseFactory,
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
    with django_assert_max_num_queries(6):
        client.get(reverse("exercise_detail", args=[bench.pk]))


def test_catalog_query_budget(client, user, django_assert_max_num_queries):
    fill_history(user)

    client.force_login(user)
    with django_assert_max_num_queries(7):
        client.get(reverse("exercise_list"))


def test_live_screen_query_budget(client, user, django_assert_max_num_queries):
    """Живой экран не должен зависеть от числа упражнений в очереди."""
    fill_history(user)
    active = WorkoutFactory(user=user, duration_min=None)
    for number, exercise in enumerate(ExerciseFactory.create_batch(3), start=1):
        StrengthSetFactory(workout=active, exercise=exercise, set_number=number, done=False)

    client.force_login(user)
    with django_assert_max_num_queries(12):
        client.get(reverse("workout_live", args=[active.pk]))


def test_profile_query_budget(client, user, django_assert_max_num_queries):
    fill_history(user)

    client.force_login(user)
    with django_assert_max_num_queries(9):
        client.get(reverse("profile"))
