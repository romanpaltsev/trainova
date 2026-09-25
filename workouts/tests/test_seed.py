"""Команда seed должна быть идемпотентной."""

import pytest
from django.core.management import call_command

from accounts.tests.factories import UserFactory
from workouts.management.commands.seed import CHANGELOG, EXERCISES, SPORTS
from workouts.models import EXERCISE_NAME_MAX_LENGTH, ChangelogEntry, Exercise, Sport
from workouts.tests.test_precise_exercise_names import load_migration

pytestmark = pytest.mark.django_db

# Снаряды справочника: закрытый набор, хотя поле — свободный текст. Опечатка
# («Гантеля») дала бы седьмой чип в каталоге, и заметить её глазами в списке из
# шести десятков строк нельзя.
EQUIPMENT = {"Штанга", "Гантели", "Тренажёр", "Блок", "Гиря", "Своё тело"}

PRECISE = load_migration().PRECISE


@pytest.fixture(autouse=True)
def without_shipped_announcements():
    """seed отвечает только за стартовые новости; анонсы релизов приезжают
    data-миграциями и в тестовой базе уже есть — считать их вместе с CHANGELOG
    нельзя."""
    ChangelogEntry.objects.all().delete()


def test_seed_creates_global_catalogs():
    call_command("seed")

    assert Sport.objects.global_only().count() == len(SPORTS)
    assert Exercise.objects.global_only().count() == len(EXERCISES)
    assert Sport.objects.get(name="Силовая").category == Sport.Category.STRENGTH
    assert Sport.objects.get(name="Бег").category == Sport.Category.CARDIO


def test_seed_creates_starter_changelog_entries():
    call_command("seed")

    entries = ChangelogEntry.objects.published()
    assert entries.count() == len(CHANGELOG)
    assert entries.filter(kind=ChangelogEntry.Kind.FIX).count() == 1
    assert entries.first().title == "Тёмная и светлая темы"  # порядок — новые сверху


def test_seed_is_idempotent():
    call_command("seed")
    sports = set(Sport.objects.values_list("id", "name", "category"))
    # equipment в разрезе обязателен: без него тест пропустил бы регресс
    # «повторный запуск затирает снаряд».
    exercises = set(Exercise.objects.values_list("id", "name", "muscle_group", "equipment"))
    news = set(ChangelogEntry.objects.values_list("id", "title", "published_at"))

    call_command("seed")

    assert set(Sport.objects.values_list("id", "name", "category")) == sports
    assert set(Exercise.objects.values_list("id", "name", "muscle_group", "equipment")) == exercises
    assert set(ChangelogEntry.objects.values_list("id", "title", "published_at")) == news


def test_catalog_is_professional_enough():
    """Справочник — данные, и проверять их стоит целиком, а не на выбор.

    Размер задан вилкой, а не числом: список правится, и тест должен ловить
    «половина списка потерялась», а не каждую новую запись.
    """
    names = [name for name, _group, _equipment in EXERCISES]

    assert 60 <= len(EXERCISES) <= 80
    # Уникальность без учёта регистра — ровно то, что проверяет unique_global_exercise_name.
    assert len(names) == len({name.lower() for name in names})
    assert all(len(name) <= EXERCISE_NAME_MAX_LENGTH for name in names)
    assert all(group and equipment for _name, group, equipment in EXERCISES)
    assert {equipment for _name, _group, equipment in EXERCISES} == EQUIPMENT


def test_seed_fills_equipment_of_existing_global_exercise():
    """Записи из старого справочника уже есть в базе — снаряд им проставляет seed."""
    Exercise.objects.create(name="Планка", muscle_group="Пресс", owner=None)

    call_command("seed")

    plank = Exercise.objects.global_only().get(name__iexact="Планка")
    assert plank.equipment == "Своё тело"
    assert Exercise.objects.global_only().count() == len(EXERCISES)


def test_seed_keeps_equipment_edited_by_admin():
    """Непустой снаряд — решение админа, повторный запуск его не откатывает."""
    call_command("seed")
    exercise = Exercise.objects.global_only().get(name="Подтягивания")
    exercise.equipment = "Турник"
    exercise.save(update_fields=["equipment"])

    call_command("seed")

    exercise.refresh_from_db()
    assert exercise.equipment == "Турник"


def test_seed_does_not_rename_existing_exercises():
    """Переименование глобальных — дело миграции: она обратима и видна в истории."""
    Exercise.objects.create(name="Жим лёжа", muscle_group="Грудь", owner=None)

    call_command("seed")

    assert Exercise.objects.global_only().filter(name="Жим лёжа").exists()
    assert Exercise.objects.global_only().count() == len(EXERCISES) + 1


def test_rename_map_targets_are_in_the_catalog():
    """Миграция 0034 и seed обязаны сойтись: иначе переименованное имя не в списке.

    Разъехавшись, они дали бы дубль: seed создал бы «Жим штанги лёжа» рядом с
    переименованной записью, и история разошлась бы по двум строкам.
    """
    targets = {target.lower() for _source, target, _equipment in PRECISE}
    names = {name.lower() for name, _group, _equipment in EXERCISES}

    assert targets <= names
    # И снаряд у общих записей тот же: иначе seed и миграция спорили бы за поле.
    by_name = {name.lower(): equipment for name, _group, equipment in EXERCISES}
    assert all(by_name[target.lower()] == equipment for _source, target, equipment in PRECISE)


def test_seed_keeps_edited_changelog_entry():
    """Правки админа в существующей записи команда не перезаписывает."""
    call_command("seed")
    entry = ChangelogEntry.objects.get(title="Таймер отдыха")
    entry.body = "Текст, отредактированный админом."
    entry.save(update_fields=["body"])

    call_command("seed")

    entry.refresh_from_db()
    assert entry.body == "Текст, отредактированный админом."
    assert ChangelogEntry.objects.filter(title="Таймер отдыха").count() == 1


def test_seed_fixes_category_of_existing_global_sport():
    """Если категория глобального вида спорта разъехалась со seed — она выправляется."""
    Sport.objects.create(name="Силовая", category=Sport.Category.CARDIO, owner=None)

    call_command("seed")

    assert Sport.objects.filter(name__iexact="Силовая").count() == 1
    assert Sport.objects.get(name="Силовая").category == Sport.Category.STRENGTH


def test_seed_does_not_touch_user_records():
    """Личный вид спорта пользователя seed не переиспользует и не правит."""
    user = UserFactory()
    own = Sport.objects.create(name="Силовая", category=Sport.Category.CARDIO, owner=user)

    call_command("seed")

    own.refresh_from_db()
    assert own.category == Sport.Category.CARDIO
    assert Sport.objects.global_only().get(name="Силовая").category == Sport.Category.STRENGTH
