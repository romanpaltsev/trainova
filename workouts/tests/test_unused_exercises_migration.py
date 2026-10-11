"""Миграция 0084: чистка справочника от упражнений без тренировок.

Удаляется всё, что не участвует ни в одной тренировке, — и общее, и личное у
всех. Остаётся упражнение с подходом в любой тренировке (и в черновике), с
заметкой или с ожидающей заявкой в общий справочник.
"""

import importlib.util
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.conf import settings

from workouts.models import CatalogRequest, Exercise, ExerciseMachine, ExerciseSettings
from workouts.tests.factories import (
    ExerciseFactory,
    ExerciseMachineFactory,
    ExerciseNoteFactory,
    ExerciseSettingsFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def run_migration():
    path = Path(settings.BASE_DIR) / "workouts" / "migrations" / "0084_delete_unused_exercises.py"
    spec = importlib.util.spec_from_file_location("delete_unused_exercises", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.delete_unused(django_apps, None)


def test_unused_exercises_are_deleted_with_their_settings(user):
    shared = ExerciseFactory(name="Общее без тренировок", owner=None)
    ExerciseFactory(name="Своё без тренировок", owner=user)
    ExerciseSettingsFactory(user=user, exercise=shared)

    run_migration()

    assert not Exercise.objects.exists()
    assert not ExerciseSettings.objects.exists()


def test_exercises_taking_part_in_workouts_stay(user, other_user):
    done = ExerciseFactory(name="Делал", owner=None)
    StrengthSetFactory(workout=WorkoutFactory(user=user), exercise=done, set_number=1)
    planned = ExerciseFactory(name="В черновике", owner=user)
    draft = WorkoutFactory(user=user, started_at=None, duration_min=None)
    StrengthSetFactory(workout=draft, exercise=planned, set_number=1, done=False)
    # Общее, которое делал только другой пользователь, — тоже используется.
    theirs = ExerciseFactory(name="Делал другой", owner=None)
    StrengthSetFactory(workout=WorkoutFactory(user=other_user), exercise=theirs, set_number=1)
    noted = ExerciseFactory(name="С заметкой", owner=user)
    ExerciseNoteFactory(workout=WorkoutFactory(user=user), exercise=noted, text="узкий хват")
    # Тренажёр указан — упражнение собираются делать (маятниковый присед на проде).
    pendulum = ExerciseFactory(name="С тренажёром", owner=None)
    ExerciseMachineFactory(user=user, exercise=pendulum)
    proposed = ExerciseFactory(name="На рассмотрении", owner=user)
    CatalogRequest.objects.create(user=user, kind="exercise", exercise=proposed)
    rejected = ExerciseFactory(name="Отклонённое", owner=user)
    CatalogRequest.objects.create(
        user=user, kind="exercise", exercise=rejected, status="rejected", reason="Дубль"
    )

    run_migration()

    assert set(Exercise.objects.values_list("name", flat=True)) == {
        "Делал",
        "В черновике",
        "Делал другой",
        "С заметкой",
        "С тренажёром",
        "На рассмотрении",
    }
    assert ExerciseMachine.objects.count() == 1
