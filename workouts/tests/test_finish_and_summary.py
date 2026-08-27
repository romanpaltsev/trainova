"""Завершение живого режима и экран-итог силовой тренировки."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.models import Workout
from workouts.tests.factories import (
    CardioDetailsFactory,
    ExerciseFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def active_started_ago(user, minutes):
    return WorkoutFactory(
        user=user, duration_min=None, started_at=timezone.now() - timedelta(minutes=minutes)
    )


def test_finish_computes_duration_and_deletes_undone_sets(client, user):
    client.force_login(user)
    workout = active_started_ago(user, 40)
    exercise = ExerciseFactory()
    done = StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=True)
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=2, done=False)

    response = client.post(reverse("workout_finish", args=[workout.pk]))

    workout.refresh_from_db()
    assert response.status_code == 302
    assert response.url == reverse("workout_summary", args=[workout.pk])
    assert workout.duration_min in (40, 41)
    assert list(workout.sets.all()) == [done]


def test_finish_duration_is_at_least_one_minute(client, user):
    client.force_login(user)
    workout = active_started_ago(user, 0)
    StrengthSetFactory(workout=workout, set_number=1, done=True)

    client.post(reverse("workout_finish", args=[workout.pk]))

    workout.refresh_from_db()
    assert workout.duration_min == 1


def test_finish_with_no_done_sets_deletes_workout(client, user):
    client.force_login(user)
    workout = active_started_ago(user, 10)
    StrengthSetFactory(workout=workout, set_number=1, done=False)

    response = client.post(reverse("workout_finish", args=[workout.pk]))

    assert response.status_code == 302
    assert response.url == reverse("workout_history")
    assert not Workout.objects.filter(pk=workout.pk).exists()


def test_finish_twice_is_idempotent(client, user):
    client.force_login(user)
    workout = active_started_ago(user, 30)
    StrengthSetFactory(workout=workout, set_number=1, done=True)

    client.post(reverse("workout_finish", args=[workout.pk]))
    workout.refresh_from_db()
    duration = workout.duration_min

    response = client.post(reverse("workout_finish", args=[workout.pk]))

    workout.refresh_from_db()
    assert response.status_code == 302
    assert workout.duration_min == duration


def test_finish_of_other_users_workout_is_404(client, user, other_user):
    client.force_login(user)
    alien = WorkoutFactory(user=other_user, duration_min=None)

    response = client.post(reverse("workout_finish", args=[alien.pk]))

    alien.refresh_from_db()
    assert response.status_code == 404
    assert alien.duration_min is None


def test_finish_modal_shows_done_count(client, user):
    client.force_login(user)
    workout = active_started_ago(user, 10)
    exercise = ExerciseFactory()
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=True)
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=2, done=True)

    content = client.get(reverse("workout_finish", args=[workout.pk])).content.decode()

    assert "Выполнено подходов: 2" in content


def test_summary_shows_exercises_and_total_tonnage(client, user):
    client.force_login(user)
    workout = WorkoutFactory(user=user, duration_min=62)
    bench = ExerciseFactory(name="Жим лёжа")
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1, weight_kg=80, reps=8)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=2, weight_kg=80, reps=8)

    content = client.get(reverse("workout_summary", args=[workout.pk])).content.decode()

    assert "Жим лёжа" in content
    assert "1280" in content  # 2 × 80 кг × 8
    assert "1:02" in content


def test_summary_of_active_workout_redirects_to_live(client, user):
    client.force_login(user)
    workout = WorkoutFactory(user=user, duration_min=None)

    response = client.get(reverse("workout_summary", args=[workout.pk]))

    assert response.status_code == 302
    assert response.url == reverse("workout_live", args=[workout.pk])


def test_summary_of_other_users_workout_is_404(client, user, other_user):
    client.force_login(user)
    alien = WorkoutFactory(user=other_user)

    response = client.get(reverse("workout_summary", args=[alien.pk]))

    assert response.status_code == 404


def test_summary_for_cardio_is_404(client, user):
    client.force_login(user)
    cardio = CardioDetailsFactory(workout__user=user).workout

    response = client.get(reverse("workout_summary", args=[cardio.pk]))

    assert response.status_code == 404
