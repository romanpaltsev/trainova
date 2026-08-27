"""Гибридные справочники: глобальное видно всем, чужое личное — никому."""

import pytest
from django.db import IntegrityError, transaction

from accounts.tests.factories import UserFactory
from workouts.models import Exercise, Sport
from workouts.tests.factories import ExerciseFactory, SportFactory

pytestmark = pytest.mark.django_db

CATALOGS = [
    pytest.param(Sport, SportFactory, id="sport"),
    pytest.param(Exercise, ExerciseFactory, id="exercise"),
]


@pytest.mark.parametrize(("model", "factory"), CATALOGS)
def test_visible_to_returns_global_and_own_records(model, factory):
    alice, bob = UserFactory(), UserFactory()
    glob = factory(owner=None)
    alice_own = factory(owner=alice)
    bob_own = factory(owner=bob)

    visible = set(model.objects.visible_to(alice))

    assert visible == {glob, alice_own}
    assert bob_own not in visible


@pytest.mark.parametrize(("model", "factory"), CATALOGS)
def test_visible_to_is_chainable(model, factory):
    alice = UserFactory()
    factory(owner=alice, name="Своё")
    factory(owner=None, name="Глобальное")

    assert model.objects.visible_to(alice).filter(name="Своё").count() == 1


@pytest.mark.parametrize(("model", "factory"), CATALOGS)
def test_same_name_allowed_for_different_owners(model, factory):
    alice, bob = UserFactory(), UserFactory()

    factory(owner=None, name="Гребля")
    factory(owner=alice, name="Гребля")
    factory(owner=bob, name="Гребля")

    assert model.objects.filter(name="Гребля").count() == 3


@pytest.mark.parametrize(("model", "factory"), CATALOGS)
@pytest.mark.parametrize("second_name", ["Гребля", "гребля", "ГРЕБЛЯ"])
def test_duplicate_name_for_same_owner_is_rejected(model, factory, second_name):
    alice = UserFactory()
    factory(owner=alice, name="Гребля")

    with pytest.raises(IntegrityError), transaction.atomic():
        factory(owner=alice, name=second_name)


@pytest.mark.parametrize(("model", "factory"), CATALOGS)
@pytest.mark.parametrize("second_name", ["Гребля", "гребля"])
def test_duplicate_global_name_is_rejected(model, factory, second_name):
    factory(owner=None, name="Гребля")

    with pytest.raises(IntegrityError), transaction.atomic():
        factory(owner=None, name=second_name)


@pytest.mark.parametrize(("model", "factory"), CATALOGS)
def test_deleting_owner_removes_only_own_records(model, factory):
    alice = UserFactory()
    glob = factory(owner=None)
    factory(owner=alice)

    alice.delete()

    assert list(model.objects.all()) == [glob]


def test_is_global_flag():
    assert SportFactory(owner=None).is_global is True
    assert SportFactory(owner=UserFactory()).is_global is False
