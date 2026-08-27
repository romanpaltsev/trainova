"""Кнопка «Повторить»: новая активная тренировка с тем же набором упражнений."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts import services
from workouts.models import Sport, Workout
from workouts.tests.factories import (
    CardioDetailsFactory,
    ExerciseFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def days_ago(days):
    return timezone.now() - timedelta(days=days)


def test_repeat_creates_active_workout_with_source_exercises_in_order(client, user):
    client.force_login(user)
    bench = ExerciseFactory(name="Жим лёжа")
    squat = ExerciseFactory(name="Присед")
    source = WorkoutFactory(user=user, started_at=days_ago(7))
    # Присед добавлен первым: порядок повторения — порядок добавления, не алфавит.
    StrengthSetFactory(workout=source, exercise=squat, set_number=1, weight_kg=100, reps=5)
    StrengthSetFactory(workout=source, exercise=bench, set_number=1, weight_kg=70, reps=10)

    response = client.post(reverse("workout_repeat", args=[source.pk]))

    workout = Workout.objects.get(user=user, duration_min__isnull=True)
    assert response.status_code == 302
    assert response.url == reverse("workout_live", args=[workout.pk])
    groups = services.exercise_groups(workout)
    assert [g["exercise"] for g in groups] == [squat, bench]
    assert all(not s.done for g in groups for s in g["sets"])


def test_repeat_prefills_from_latest_workout_not_from_source(client, user):
    client.force_login(user)
    bench = ExerciseFactory()
    source = WorkoutFactory(user=user, started_at=days_ago(14))
    StrengthSetFactory(workout=source, exercise=bench, set_number=1, weight_kg=60, reps=10)
    latest = WorkoutFactory(user=user, started_at=days_ago(2))
    StrengthSetFactory(workout=latest, exercise=bench, set_number=1, weight_kg=70, reps=8)

    client.post(reverse("workout_repeat", args=[source.pk]))

    workout = Workout.objects.get(user=user, duration_min__isnull=True)
    assert [(s.weight_kg, s.reps) for s in workout.sets.all()] == [(70, 8)]


def test_repeat_with_active_workout_redirects_to_it(client, user):
    client.force_login(user)
    source = WorkoutFactory(user=user)
    active = WorkoutFactory(user=user, duration_min=None)

    response = client.post(reverse("workout_repeat", args=[source.pk]))

    assert response.status_code == 302
    assert response.url == reverse("workout_live", args=[active.pk])
    assert Workout.objects.filter(user=user, duration_min__isnull=True).count() == 1


def test_repeat_of_other_users_workout_is_404(client, user, other_user):
    client.force_login(user)
    alien = WorkoutFactory(user=other_user)

    response = client.post(reverse("workout_repeat", args=[alien.pk]))

    assert response.status_code == 404
    assert not Workout.objects.filter(user=user).exists()


def test_repeat_of_cardio_or_unfinished_is_404(client, user):
    client.force_login(user)
    cardio = CardioDetailsFactory(workout__user=user).workout
    unfinished = WorkoutFactory(user=user, duration_min=None)

    assert client.post(reverse("workout_repeat", args=[cardio.pk])).status_code == 404
    assert client.post(reverse("workout_repeat", args=[unfinished.pk])).status_code == 404


def test_history_strength_card_has_repeat_and_open_buttons(client, user):
    client.force_login(user)
    strength = WorkoutFactory(user=user)
    cardio = CardioDetailsFactory(workout__user=user).workout

    content = client.get(reverse("workout_history")).content.decode()

    assert reverse("workout_repeat", args=[strength.pk]) in content
    assert reverse("workout_summary", args=[strength.pk]) in content
    # Кардио-карточка — ссылка на правку, кнопок повторения у неё нет.
    assert reverse("workout_repeat", args=[cardio.pk]) not in content


def test_repeat_button_starts_same_sport(client, user):
    client.force_login(user)
    sport = Sport.objects.create(name="Кроссфит", category=Sport.Category.STRENGTH, owner=user)
    source = WorkoutFactory(user=user, sport=sport)
    StrengthSetFactory(workout=source, set_number=1)

    client.post(reverse("workout_repeat", args=[source.pk]))

    workout = Workout.objects.get(user=user, duration_min__isnull=True)
    assert workout.sport == sport
    assert workout.rest_seconds is None
