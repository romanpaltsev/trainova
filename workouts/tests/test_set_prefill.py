"""Подстановка подходов из последней тренировки с этим упражнением (живой режим)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from workouts import services
from workouts.tests.factories import ExerciseFactory, StrengthSetFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


def days_ago(days):
    return timezone.now() - timedelta(days=days)


def test_prefill_copies_latest_finished_workout(user):
    exercise = ExerciseFactory()
    old = WorkoutFactory(user=user, started_at=days_ago(7))
    StrengthSetFactory(workout=old, exercise=exercise, set_number=1, weight_kg=60, reps=10)
    latest = WorkoutFactory(user=user, started_at=days_ago(2))
    StrengthSetFactory(workout=latest, exercise=exercise, set_number=1, weight_kg=70, reps=10)
    StrengthSetFactory(
        workout=latest, exercise=exercise, set_number=2, weight_kg=Decimal("77.5"), reps=8
    )
    StrengthSetFactory(workout=latest, exercise=exercise, set_number=3, weight_kg=80, reps=5)
    active = WorkoutFactory(user=user, duration_min=None)

    rows = services.create_planned_sets(active, exercise)

    assert [(row.set_number, row.weight_kg, row.reps, row.done) for row in rows] == [
        (1, Decimal("70"), 10, False),
        (2, Decimal("77.5"), 8, False),
        (3, Decimal("80"), 5, False),
    ]
    assert all(row.workout == active for row in rows)


def test_prefill_renumbers_sets_from_one(user):
    """В источнике могли остаться пропуски номеров — копия нумеруется заново."""
    exercise = ExerciseFactory()
    latest = WorkoutFactory(user=user)
    StrengthSetFactory(workout=latest, exercise=exercise, set_number=2, weight_kg=50, reps=12)
    StrengthSetFactory(workout=latest, exercise=exercise, set_number=5, weight_kg=55, reps=10)
    active = WorkoutFactory(user=user, duration_min=None)

    rows = services.create_planned_sets(active, exercise)

    assert [row.set_number for row in rows] == [1, 2]


def test_prefill_first_time_gives_one_empty_set(user):
    exercise = ExerciseFactory()
    active = WorkoutFactory(user=user, duration_min=None)

    rows = services.create_planned_sets(active, exercise)

    assert [(row.set_number, row.weight_kg, row.reps, row.done) for row in rows] == [
        (1, 0, 0, False)
    ]


def test_prefill_ignores_other_users_history(user, other_user):
    exercise = ExerciseFactory()
    alien = WorkoutFactory(user=other_user)
    StrengthSetFactory(workout=alien, exercise=exercise, set_number=1, weight_kg=100, reps=5)
    active = WorkoutFactory(user=user, duration_min=None)

    rows = services.create_planned_sets(active, exercise)

    assert [(row.weight_kg, row.reps) for row in rows] == [(0, 0)]


def test_prefill_ignores_unfinished_workouts(user):
    exercise = ExerciseFactory()
    finished = WorkoutFactory(user=user, started_at=days_ago(5))
    StrengthSetFactory(workout=finished, exercise=exercise, set_number=1, weight_kg=60, reps=10)
    active = WorkoutFactory(user=user, duration_min=None, started_at=days_ago(0))
    StrengthSetFactory(workout=active, exercise=exercise, set_number=1, weight_kg=99, reps=1)

    previous = services.last_sets(user, exercise)

    assert [(row.weight_kg, row.reps) for row in previous] == [(Decimal("60"), 10)]


def test_prefill_ignores_other_exercises_sets(user):
    bench = ExerciseFactory()
    squat = ExerciseFactory()
    latest = WorkoutFactory(user=user)
    StrengthSetFactory(workout=latest, exercise=bench, set_number=1, weight_kg=70, reps=10)
    StrengthSetFactory(workout=latest, exercise=squat, set_number=1, weight_kg=100, reps=5)
    active = WorkoutFactory(user=user, duration_min=None)

    rows = services.create_planned_sets(active, bench)

    assert [(row.weight_kg, row.reps) for row in rows] == [(Decimal("70"), 10)]


def test_prefill_latest_is_by_started_at_with_id_tiebreak(user):
    """При равном started_at последней считается тренировка с бóльшим id — как в ленте."""
    exercise = ExerciseFactory()
    moment = days_ago(3)
    first = WorkoutFactory(user=user, started_at=moment)
    StrengthSetFactory(workout=first, exercise=exercise, set_number=1, weight_kg=60, reps=10)
    second = WorkoutFactory(user=user, started_at=moment)
    StrengthSetFactory(workout=second, exercise=exercise, set_number=1, weight_kg=65, reps=8)

    previous = services.last_sets(user, exercise)

    assert [(row.weight_kg, row.reps) for row in previous] == [(Decimal("65"), 8)]
