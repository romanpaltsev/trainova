"""«Создать» в справочнике: своё упражнение, а у администратора — и общее.

В отличие от живого режима, совпавшее имя здесь — ошибка формы, а не «возьми
найденное»: в справочнике упражнение заводят осознанно.
"""

import pytest
from django.urls import reverse

from workouts.models import Exercise
from workouts.tests.budgets import SIDEBAR_QUERIES
from workouts.tests.factories import ExerciseFactory

pytestmark = pytest.mark.django_db

URL = reverse("exercise_create")


@pytest.fixture
def admin_user(user):
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    return user


def create(client, **data):
    return client.post(URL, data, HTTP_HX_REQUEST="true")


def test_catalog_has_create_button(client, user):
    client.force_login(user)

    html = client.get(reverse("exercise_list")).content.decode()

    assert f'hx-get="{URL}"' in html


def test_modal_opens(client, user):
    client.force_login(user)

    response = client.get(URL, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert "Новое упражнение" in response.content.decode()
    assert 'name="scope"' not in response.content.decode()  # «Общее» — только админу


def test_creates_own_exercise_with_facets_and_opens_it(client, user):
    client.force_login(user)

    response = create(
        client,
        name="  Пуловер  ",
        measurement="reps",
        muscle_group_own="Грудь",
        equipment="Гантели",
    )

    exercise = Exercise.objects.get(name="Пуловер")
    assert exercise.owner == user
    assert exercise.measurement == "reps"
    assert exercise.muscle_group == "Грудь"
    assert response.headers["HX-Redirect"] == reverse("exercise_detail", args=[exercise.pk])


@pytest.mark.parametrize("owner", ["global", "own"])
def test_existing_name_is_an_error_not_a_match(client, user, owner):
    ExerciseFactory(name="Жим лёжа", owner=None if owner == "global" else user)
    client.force_login(user)

    response = create(client, name="жим ЛЁЖА")

    assert "HX-Redirect" not in response.headers
    assert "Такое упражнение уже есть" in response.content.decode()
    assert Exercise.objects.filter(name__iexact="жим лёжа").count() == 1


def test_someone_elses_name_does_not_block(client, user, other_user):
    """Чужое личное упражнение не видно — и создать своё с тем же именем можно."""
    ExerciseFactory(name="Тяга Кинга", owner=other_user)
    client.force_login(user)

    create(client, name="Тяга Кинга")

    assert Exercise.objects.filter(name="Тяга Кинга", owner=user).exists()


def test_regular_user_cannot_create_global(client, user):
    client.force_login(user)

    create(client, name="Хитрое упражнение", scope="global")

    assert Exercise.objects.get(name="Хитрое упражнение").owner == user


def test_admin_creates_global_exercise(client, admin_user, other_user):
    client.force_login(admin_user)

    create(client, name="Жим Свенда", scope="global", muscle_group="Грудь")

    exercise = Exercise.objects.get(name="Жим Свенда")
    assert exercise.is_global
    client.force_login(other_user)
    assert exercise in Exercise.objects.visible_to(other_user)


def test_admin_global_name_must_be_unique_among_globals(client, admin_user):
    ExerciseFactory(name="Планка", owner=None)
    client.force_login(admin_user)

    response = create(client, name="планка", scope="global")

    assert "Такое упражнение уже есть" in response.content.decode()
    assert Exercise.objects.global_only().filter(name__iexact="планка").count() == 1


def test_guest_is_sent_to_login(client):
    response = client.get(URL)

    assert response.status_code == 302
    assert response.url.startswith(reverse("account_login"))


def test_catalog_query_budget_is_unchanged(client, user, django_assert_max_num_queries):
    client.force_login(user)

    with django_assert_max_num_queries(7 + SIDEBAR_QUERIES):
        client.get(reverse("exercise_list"))
