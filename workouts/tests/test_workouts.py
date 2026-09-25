"""Изоляция тренировок по пользователю и целостность данных."""

from datetime import UTC, datetime

import pytest
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError

from accounts.tests.factories import UserFactory
from workouts.models import CardioPart, Sport, StrengthSet, Workout
from workouts.tests.factories import (
    CardioPartFactory,
    ExerciseFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def test_user_does_not_see_other_users_workouts():
    alice, bob = UserFactory(), UserFactory()
    alice_workout = WorkoutFactory(user=alice)
    WorkoutFactory(user=bob)

    assert list(Workout.objects.filter(user=alice)) == [alice_workout]


def test_user_does_not_see_sets_of_other_users_workouts():
    alice, bob = UserFactory(), UserFactory()
    alice_set = StrengthSetFactory(workout__user=alice)
    StrengthSetFactory(workout__user=bob)

    assert list(StrengthSet.objects.filter(workout__user=alice)) == [alice_set]


def test_user_does_not_see_cardio_of_other_users_workouts():
    alice, bob = UserFactory(), UserFactory()
    alice_cardio = CardioPartFactory(workout__user=alice)
    CardioPartFactory(workout__user=bob)

    assert list(CardioPart.objects.filter(workout__user=alice)) == [alice_cardio]


def test_deleting_user_removes_their_workouts_with_sets_and_cardio():
    alice = UserFactory()
    StrengthSetFactory(workout__user=alice)
    CardioPartFactory(workout__user=alice)

    alice.delete()

    assert Workout.objects.count() == 0
    assert StrengthSet.objects.count() == 0
    assert CardioPart.objects.count() == 0


def test_sport_in_use_cannot_be_deleted():
    workout = WorkoutFactory()

    with pytest.raises(ProtectedError):
        workout.sport.delete()


def test_exercise_in_use_cannot_be_deleted():
    strength_set = StrengthSetFactory()

    with pytest.raises(ProtectedError):
        strength_set.exercise.delete()


def test_set_numbers_are_unique_within_exercise():
    strength_set = StrengthSetFactory(set_number=1)
    same = StrengthSet(
        workout=strength_set.workout,
        exercise=strength_set.exercise,
        set_number=1,
        weight_kg=90,
        reps=5,
    )

    with pytest.raises(ValidationError):
        same.full_clean()


def test_strength_workout_accepts_a_cardio_part():
    """Заминка на дорожке после штанги — одна тренировка, а не две.

    Запрет, который тут стоял раньше, и был причиной двух карточек в ленте за
    одно занятие. Теперь «смешанность» законна и нигде не хранится флагом: она
    выводится из того, что у тренировки есть и подходы, и части.
    """
    workout = WorkoutFactory(sport=SportFactory(category=Sport.Category.STRENGTH))
    StrengthSetFactory(workout=workout, set_number=1)
    run = SportFactory(name="Бег", category=Sport.Category.CARDIO)

    part = CardioPart(workout=workout, sport=run, duration_min=20, distance_km=4)
    part.full_clean()
    part.save()

    assert list(workout.cardio_parts.all()) == [part]
    assert workout.sets.count() == 1


def test_cardio_workout_accepts_sets():
    """Зеркально: вид спорта-хозяин кардио не мешает записать подходы."""
    workout = WorkoutFactory(sport__category=Sport.Category.CARDIO)
    strength_set = StrengthSet(
        workout=workout, exercise=ExerciseFactory(), set_number=1, weight_kg=80, reps=8
    )

    strength_set.full_clean()
    strength_set.save()

    assert workout.sets.count() == 1


def test_part_pace_is_counted_from_its_own_duration():
    """Темп части считается по её времени, а не по времени всей тренировки.

    Пять километров за 25 минут внутри полуторачасовой тренировки — это 5:00, а
    не 18:00. Раньше делили на длительность тренировки, и у смешанной рекорд
    темпа стал бы ложным без единой ошибки в логах.
    """
    workout = WorkoutFactory(sport=SportFactory(category=Sport.Category.STRENGTH), duration_min=90)
    run = SportFactory(name="Бег", category=Sport.Category.CARDIO)

    part = CardioPart.objects.create(workout=workout, sport=run, duration_min=25, distance_km=5)

    assert part.pace_display == "5:00"


def test_tonnage_of_set():
    assert StrengthSetFactory(weight_kg=80, reps=8).tonnage_kg == 640


def test_workout_str_uses_project_timezone():
    """started_at хранится в UTC; в названии тренировки должно быть местное время."""
    workout = WorkoutFactory(
        sport=SportFactory(name="Силовая"),
        started_at=datetime(2026, 8, 27, 15, 30, tzinfo=UTC),
    )

    assert str(workout) == "Силовая — 27.08.2026 18:30"
