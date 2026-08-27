"""Команда seed должна быть идемпотентной."""

import pytest
from django.core.management import call_command

from accounts.tests.factories import UserFactory
from workouts.management.commands.seed import EXERCISES, SPORTS
from workouts.models import Exercise, Sport

pytestmark = pytest.mark.django_db


def test_seed_creates_global_catalogs():
    call_command("seed")

    assert Sport.objects.global_only().count() == len(SPORTS)
    assert Exercise.objects.global_only().count() == len(EXERCISES)
    assert Sport.objects.get(name="Силовая").category == Sport.Category.STRENGTH
    assert Sport.objects.get(name="Бег").category == Sport.Category.CARDIO


def test_seed_is_idempotent():
    call_command("seed")
    sports = set(Sport.objects.values_list("id", "name", "category"))
    exercises = set(Exercise.objects.values_list("id", "name", "muscle_group"))

    call_command("seed")

    assert set(Sport.objects.values_list("id", "name", "category")) == sports
    assert set(Exercise.objects.values_list("id", "name", "muscle_group")) == exercises


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
