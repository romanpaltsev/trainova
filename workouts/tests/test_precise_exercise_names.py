"""Миграция 0034: точные имена глобальных упражнений и снаряд у них.

Главное, что здесь проверяется, — история цела. Переименование выбрано именно
потому, что это та же строка БД: подходы, рекорды и графики продолжают смотреть
на неё. Если бы миграция создавала запись заново, история «Жима лёжа»
осталась бы висеть на старом названии, и тесты ниже это поймают.
"""

import importlib.util
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.conf import settings

from workouts.models import Exercise, StrengthSet
from workouts.tests.factories import ExerciseFactory, StrengthSetFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


def load_migration():
    """Модуль миграции грузим по пути: имя с цифрами не импортируется обычным путём."""
    path = Path(settings.BASE_DIR) / "workouts" / "migrations" / "0034_precise_exercise_names.py"
    spec = importlib.util.spec_from_file_location("precise_exercise_names", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def with_history(user, name, **extra):
    """Глобальное упражнение с записанной тренировкой на три подхода."""
    exercise = ExerciseFactory(name=name, owner=None, **extra)
    workout = WorkoutFactory(user=user)
    for number in (1, 2, 3):
        StrengthSetFactory(workout=workout, exercise=exercise, set_number=number)
    return exercise


def test_history_survives_the_rename(user):
    """Та же строка БД: pk не меняется, подходы остаются на месте."""
    exercise = with_history(user, "Жим лёжа", muscle_group="Грудь")
    migration = load_migration()

    migration.make_precise(django_apps, None)

    exercise.refresh_from_db()
    assert exercise.name == "Жим штанги лёжа"
    assert exercise.equipment == "Штанга"
    assert exercise.muscle_group == "Грудь"
    assert StrengthSet.objects.filter(exercise=exercise).count() == 3
    # Записи-двойника не появилось: иначе история разорвалась бы пополам.
    assert Exercise.objects.global_only().count() == 1


def test_reverse_brings_the_old_name_back(user):
    migration = load_migration()
    exercise = with_history(user, "Французский жим")

    migration.make_precise(django_apps, None)
    migration.make_general(django_apps, None)

    exercise.refresh_from_db()
    assert exercise.name == "Французский жим"
    assert exercise.equipment == ""
    assert StrengthSet.objects.filter(exercise=exercise).count() == 3


def test_equipment_only_records_keep_their_names(user):
    """У четырнадцати записей имя и так точное — миграция ставит им только снаряд."""
    squat = with_history(user, "Приседания со штангой")
    plank = ExerciseFactory(name="Планка", owner=None, measurement=Exercise.Measurement.TIME)

    load_migration().make_precise(django_apps, None)

    squat.refresh_from_db()
    plank.refresh_from_db()
    assert (squat.name, squat.equipment) == ("Приседания со штангой", "Штанга")
    assert (plank.name, plank.equipment) == ("Планка", "Своё тело")
    # Единицу измерения миграция не трогает: у планки это время, как и было.
    assert plank.measurement == Exercise.Measurement.TIME


def test_personal_exercises_are_never_touched(user, other_user):
    """Имя, придуманное человеком, — его данные: миграция правит только глобальные."""
    mine = ExerciseFactory(name="Жим лёжа", owner=user, muscle_group="Моя группа")
    alien = ExerciseFactory(name="Французский жим", owner=other_user)
    globalny = ExerciseFactory(name="Жим лёжа", owner=None)

    load_migration().make_precise(django_apps, None)

    mine.refresh_from_db()
    alien.refresh_from_db()
    globalny.refresh_from_db()
    assert (mine.name, mine.equipment) == ("Жим лёжа", "")
    assert (alien.name, alien.equipment) == ("Французский жим", "")
    assert globalny.name == "Жим штанги лёжа"


def test_taken_target_name_leaves_the_rename_undone(user):
    """Админ мог завести точное имя руками: тогда ставим только снаряд, а не падаем.

    Без этой ветки миграция упёрлась бы в unique_global_exercise_name, и деплой
    встал бы на выкатке.
    """
    old = with_history(user, "Жим лёжа")
    precise = ExerciseFactory(name="Жим штанги лёжа", owner=None)

    load_migration().make_precise(django_apps, None)

    old.refresh_from_db()
    precise.refresh_from_db()
    assert old.name == "Жим лёжа"
    assert old.equipment == "Штанга"
    assert precise.name == "Жим штанги лёжа"
    assert StrengthSet.objects.filter(exercise=old).count() == 3


def test_missing_records_are_skipped():
    """База могла не знать половины списка — пропуск, а не ошибка."""
    only = ExerciseFactory(name="Планка", owner=None)

    load_migration().make_precise(django_apps, None)

    only.refresh_from_db()
    assert only.equipment == "Своё тело"
    assert Exercise.objects.global_only().count() == 1


def test_every_target_name_is_unique_and_fits_the_column():
    """Карта переименования — данные, и их стоит проверить целиком."""
    migration = load_migration()
    targets = [target for _source, target, _equipment in migration.PRECISE]

    assert len(targets) == len({target.lower() for target in targets})
    assert all(len(target) <= 80 for target in targets)
    assert all(equipment for _source, _target, equipment in migration.PRECISE)
