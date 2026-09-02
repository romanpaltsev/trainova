"""Справочник мест: имя уникально у владельца, дефолт ровно один, чужого не видно."""

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from accounts.tests.factories import UserFactory
from workouts.models import Location, collapse_spaces
from workouts.tests.factories import LocationFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("second_name", ["СпортЛайф", "спортлайф", "СПОРТЛАЙФ"])
def test_location_name_is_unique_per_owner(second_name):
    """Регистр не создаёт второе место: иначе появятся «Дома» и «дома»."""
    alice = UserFactory()
    LocationFactory(owner=alice, name="СпортЛайф")

    with pytest.raises(IntegrityError), transaction.atomic():
        LocationFactory(owner=alice, name=second_name)


def test_same_location_name_is_allowed_for_another_user():
    """Имя уникально в пределах владельца, а не во всей базе."""
    alice, bob = UserFactory(), UserFactory()

    LocationFactory(owner=alice, name="СпортЛайф")
    LocationFactory(owner=bob, name="СпортЛайф")

    assert Location.objects.filter(name="СпортЛайф").count() == 2


def test_location_requires_owner():
    """Глобальных мест не бывает: чужой «СпортЛайф» — не мой."""
    with pytest.raises(IntegrityError), transaction.atomic():
        Location.objects.create(name="Ничей зал")


def test_only_one_default_per_owner():
    alice = UserFactory()
    LocationFactory(owner=alice, name="СпортЛайф", is_default=True)

    with pytest.raises(IntegrityError), transaction.atomic():
        LocationFactory(owner=alice, name="Дома", is_default=True)


def test_each_user_has_his_own_default():
    """Индекс частичный, но по владельцу: «один дефолт во всей базе» — ошибка."""
    alice, bob = UserFactory(), UserFactory()

    LocationFactory(owner=alice, name="СпортЛайф", is_default=True)
    LocationFactory(owner=bob, name="Своя качалка", is_default=True)

    assert Location.objects.filter(is_default=True).count() == 2


def test_default_for_returns_only_own_default():
    alice, bob = UserFactory(), UserFactory()
    mine = LocationFactory(owner=alice, name="СпортЛайф", is_default=True)
    LocationFactory(owner=bob, name="Чужой зал", is_default=True)

    assert Location.objects.default_for(alice) == mine


def test_default_for_returns_none_without_default():
    alice = UserFactory()
    LocationFactory(owner=alice, name="СпортЛайф")

    assert Location.objects.default_for(alice) is None


def test_deleting_owner_removes_his_locations():
    """CASCADE: место без владельца бессмысленно (тренировок у него нет)."""
    alice = UserFactory()
    LocationFactory(owner=alice)

    alice.delete()

    assert not Location.objects.exists()


def test_workout_keeps_its_location():
    place = LocationFactory(name="СпортЛайф")
    workout = WorkoutFactory(user=place.owner, location=place)

    workout.refresh_from_db()

    assert workout.location == place
    assert list(place.workouts.all()) == [workout]


def test_workout_without_location_is_allowed():
    """NULL = место не указано: так записана вся история до появления справочника."""
    workout = WorkoutFactory()

    assert workout.location is None


def test_used_location_cannot_be_deleted():
    """PROTECT, как у вида спорта: вместо удаления есть переименование."""
    place = LocationFactory(name="СпортЛайф")
    WorkoutFactory(user=place.owner, location=place)

    with pytest.raises(ProtectedError):
        place.delete()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Спорт   лайф ", "Спорт лайф"),
        ("СпортЛайф", "СпортЛайф"),
        ("\tДома\n", "Дома"),
    ],
)
def test_collapse_spaces_normalizes_input(raw, expected):
    """Без этого «СпортЛайф » и «СпортЛайф» стали бы разными записями."""
    assert collapse_spaces(raw) == expected


def test_locations_are_ordered_with_default_first():
    alice = UserFactory()
    LocationFactory(owner=alice, name="Ялта")
    LocationFactory(owner=alice, name="Азов")
    default = LocationFactory(owner=alice, name="СпортЛайф", is_default=True)

    names = list(Location.objects.filter(owner=alice).values_list("name", flat=True))

    assert names == [default.name, "Азов", "Ялта"]
