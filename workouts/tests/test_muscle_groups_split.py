"""Группы мышц подробнее: «Руки» и «Ноги» разнесены по мышцам (решение 16.10.2026).

Стартовый набор предлагается чипами ввода всегда, фильтры справочника — только
из данных; общие упражнения раскладывает миграция 0100 по названиям.
"""

import importlib

import pytest
from django.apps import apps
from django.urls import reverse

from workouts import contributions
from workouts.models import MUSCLE_GROUPS, ChangelogEntry, Exercise, Facets, facets_for
from workouts.tests.factories import ExerciseFactory
from workouts.views import group_by_muscle

split_migration = importlib.import_module("workouts.migrations.0100_split_arm_and_leg_groups")
announce = importlib.import_module("workouts.migrations.0101_announce_muscle_groups")


# ---------- Правила миграции ----------


@pytest.mark.parametrize(
    ("name", "group", "expected"),
    [
        ("Подъём штанги на бицепс", "Руки", "Бицепс"),
        ("Молотковые сгибания с гантелями", "Руки", "Бицепс"),
        ("Французский жим со штангой лёжа", "Руки", "Трицепс"),
        ("Разгибание рук на блоке", "Руки", "Трицепс"),
        ("Жим узким хватом", "руки", "Трицепс"),
        ("Сгибание запястий со штангой", "Руки", "Предплечья"),
        ("Приседания со штангой", "Ноги", "Квадрицепс"),
        ("Жим ногами", "Ноги", "Квадрицепс"),
        ("Выпады с гантелями", "Ноги", "Квадрицепс"),
        ("Разгибание ног в тренажёре", "Ноги", "Квадрицепс"),
        ("Сгибание ног в тренажёре", "Ноги", "Бицепс бедра"),
        ("Румынская тяга", "Ноги", "Бицепс бедра"),
        ("Подъёмы на носки в тренажёре", "Ноги", "Икры"),
        ("Ягодичный мостик", "Ноги", "Ягодицы"),
        ("Что-то своё", "Руки", None),
        ("Сгибания на бицепс", "Грудь", None),
    ],
)
def test_split_rules(name, group, expected):
    assert split_migration.split_group(name, group) == expected


@pytest.mark.django_db
def test_migration_touches_only_shared_arm_and_leg_exercises(user):
    shared = ExerciseFactory(name="Подъём штанги на бицепс", owner=None, muscle_group="Руки")
    unknown = ExerciseFactory(name="Загадочное", owner=None, muscle_group="Руки")
    own = ExerciseFactory(name="Французский жим", owner=user, muscle_group="Руки")
    chest = ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")

    split_migration.split(apps, None)

    for exercise in (shared, unknown, own, chest):
        exercise.refresh_from_db()
    assert shared.muscle_group == "Бицепс"
    assert unknown.muscle_group == "Руки", "неузнанное остаётся — админ доправит чипом"
    assert own.muscle_group == "Руки", "личные — данные пользователя, их не трогаем"
    assert chest.muscle_group == "Грудь"


# ---------- Стартовый набор и порядок ----------


def test_choices_add_starter_set_and_prefer_its_spelling():
    facets = Facets(muscle_groups=["трицепс", "Своя группа"], equipment=[])

    choices = facets.muscle_group_choices

    assert choices[: len(MUSCLE_GROUPS)] == list(MUSCLE_GROUPS)
    assert choices[-1] == "Своя группа"
    assert "трицепс" not in choices


def test_catalog_sections_follow_the_starter_order():
    exercises = [
        Exercise(name="Своё", muscle_group="Ноги"),
        Exercise(name="Сгибания", muscle_group="Бицепс"),
        Exercise(name="Жим", muscle_group="Грудь"),
        Exercise(name="Без группы", muscle_group=""),
    ]

    titles = [group["title"] for group in group_by_muscle(exercises)]

    assert titles == ["Грудь", "Бицепс", "Ноги", "Без группы"]


@pytest.mark.django_db
def test_catalog_filters_stay_data_only(client, user):
    """Фильтр без единого упражнения показал бы пустой список."""
    ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")
    client.force_login(user)

    groups = client.get(reverse("exercise_list")).context["muscle_groups"]

    assert groups == ["Грудь"]
    assert facets_for(user).muscle_groups == ["Грудь"]


@pytest.mark.django_db
def test_exercise_page_and_create_window_offer_the_starter_set(client, user):
    exercise = ExerciseFactory(name="Моё", owner=user, muscle_group="")
    client.force_login(user)

    page = client.get(reverse("exercise_detail", args=[exercise.pk]))
    modal = client.get(reverse("exercise_create"))

    assert page.context["muscle_groups"] == list(MUSCLE_GROUPS)
    assert modal.context["muscle_groups"] == list(MUSCLE_GROUPS)
    assert "Трицепс" in page.content.decode()


@pytest.mark.django_db
def test_typed_group_takes_the_starter_spelling(client, user):
    exercise = ExerciseFactory(name="Моё", owner=user, muscle_group="")
    client.force_login(user)

    client.post(
        reverse("exercise_muscle_group", args=[exercise.pk]), {"muscle_group_own": " трицепс "}
    )

    exercise.refresh_from_db()
    assert exercise.muscle_group == "Трицепс"


@pytest.mark.django_db
def test_made_global_exercise_takes_the_starter_spelling(user):
    exercise = ExerciseFactory(name="Разгибания с канатом", owner=user, muscle_group="трицепс")

    contributions.make_global(exercise)

    exercise.refresh_from_db()
    assert exercise.is_global and exercise.muscle_group == "Трицепс"


@pytest.mark.django_db
def test_news_is_shipped():
    assert ChangelogEntry.objects.published().filter(title=announce.TITLE).exists()
