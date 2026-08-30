"""Команда seed должна быть идемпотентной."""

import pytest
from django.core.management import call_command

from accounts.tests.factories import UserFactory
from workouts.management.commands.seed import CHANGELOG, EXERCISES, SPORTS
from workouts.models import ChangelogEntry, Exercise, Sport

pytestmark = pytest.mark.django_db


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
    assert entries.first().title == "Подготовка тренировки заранее"  # порядок — новые сверху


def test_seed_is_idempotent():
    call_command("seed")
    sports = set(Sport.objects.values_list("id", "name", "category"))
    exercises = set(Exercise.objects.values_list("id", "name", "muscle_group"))
    news = set(ChangelogEntry.objects.values_list("id", "title", "published_at"))

    call_command("seed")

    assert set(Sport.objects.values_list("id", "name", "category")) == sports
    assert set(Exercise.objects.values_list("id", "name", "muscle_group")) == exercises
    assert set(ChangelogEntry.objects.values_list("id", "title", "published_at")) == news


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
