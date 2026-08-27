"""Создание личного вида спорта из формы тренировки (HTMX-модалка)."""

import pytest
from django.urls import reverse

from workouts.models import Sport
from workouts.tests.factories import SportFactory

pytestmark = pytest.mark.django_db


def test_modal_renders(client, user):
    client.force_login(user)

    response = client.get(reverse("sport_create"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Свой вид спорта" in content
    assert "Силовая" in content and "Кардио" in content


def test_personal_sport_is_created_for_current_user(client, user):
    client.force_login(user)

    response = client.post(
        reverse("sport_create"), {"name": "Гребля", "category": Sport.Category.CARDIO}
    )

    assert response.status_code == 200
    sport = Sport.objects.get(name="Гребля")
    assert sport.owner == user
    assert sport.category == Sport.Category.CARDIO
    # Ответ обновляет блок чипов out-of-band, новый вид спорта уже выбран.
    content = response.content.decode()
    assert 'hx-swap-oob="true"' in content
    assert f'value="{sport.pk}"' in content
    assert "checked" in content


def test_new_sport_is_available_in_cardio_form(client, user):
    client.force_login(user)
    client.post(reverse("sport_create"), {"name": "Гребля", "category": Sport.Category.CARDIO})

    queryset = client.get(reverse("cardio_create")).context["form"].fields["sport"].queryset

    assert queryset.get(name="Гребля").owner == user


def test_category_is_required(client, user):
    client.force_login(user)

    response = client.post(reverse("sport_create"), {"name": "Гребля"})

    assert "category" in response.context["form"].errors
    assert not Sport.objects.filter(name="Гребля").exists()


def test_name_is_required(client, user):
    client.force_login(user)

    response = client.post(reverse("sport_create"), {"category": Sport.Category.CARDIO})

    assert "name" in response.context["form"].errors


@pytest.mark.parametrize("name", ["Велосипед", "велосипед"])
def test_duplicate_of_visible_sport_is_rejected(client, user, name):
    client.force_login(user)
    SportFactory(name="Велосипед", category=Sport.Category.CARDIO, owner=None)

    response = client.post(
        reverse("sport_create"), {"name": name, "category": Sport.Category.CARDIO}
    )

    assert "уже есть" in str(response.context["form"].errors["name"])
    assert Sport.objects.filter(name__iexact="велосипед").count() == 1


def test_other_users_sport_name_is_allowed(client, user, other_user):
    client.force_login(user)
    SportFactory(name="Каяк", category=Sport.Category.CARDIO, owner=other_user)

    client.post(reverse("sport_create"), {"name": "Каяк", "category": Sport.Category.CARDIO})

    assert Sport.objects.filter(name="Каяк").count() == 2
    assert Sport.objects.get(name="Каяк", owner=user).owner == user


def test_anonymous_cannot_create_sport(client):
    response = client.post(
        reverse("sport_create"), {"name": "Гребля", "category": Sport.Category.CARDIO}
    )

    assert response.status_code == 302
    assert not Sport.objects.filter(name="Гребля").exists()
