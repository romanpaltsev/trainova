"""Общие упражнения правит администратор — прямо в справочнике.

Правило одно на все точки правки (editable_exercises): своё — всем, общее —
только администратору, чужое личное — никому, и администратору тоже.
"""

import pytest
from django.urls import reverse

from workouts.models import Exercise
from workouts.tests.budgets import SIDEBAR_QUERIES
from workouts.tests.factories import ExerciseFactory, StrengthSetFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user(user):
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    return user


@pytest.fixture
def shared():
    return ExerciseFactory(name="Жим штанги лёжа", owner=None, muscle_group="Грудь")


def test_admin_sees_edit_controls_on_global_exercise(client, admin_user, shared):
    client.force_login(admin_user)

    response = client.get(reverse("exercise_detail", args=[shared.pk]))

    assert response.context["can_edit"] is True
    html = response.content.decode()
    assert reverse("exercise_rename", args=[shared.pk]) in html
    assert "изменения увидят все" in html


def test_regular_user_still_cannot_edit_global(client, user, shared):
    client.force_login(user)

    response = client.get(reverse("exercise_detail", args=[shared.pk]))

    assert response.context["can_edit"] is False
    assert "изменения увидят все" not in response.content.decode()
    for name, data in [
        ("exercise_rename", {"name": "Взлом"}),
        ("exercise_measurement", {"measurement": "reps"}),
        ("exercise_muscle_group", {"muscle_group": "Спина"}),
        ("exercise_equipment", {"equipment": "Гиря"}),
    ]:
        assert client.post(reverse(name, args=[shared.pk]), data).status_code == 404
    assert client.post(reverse("exercise_delete", args=[shared.pk])).status_code == 404
    shared.refresh_from_db()
    assert shared.name == "Жим штанги лёжа"


def test_admin_edits_every_axis_of_global_exercise(client, admin_user, shared):
    client.force_login(admin_user)

    client.post(reverse("exercise_rename", args=[shared.pk]), {"name": "Жим лёжа со штангой"})
    client.post(reverse("exercise_measurement", args=[shared.pk]), {"measurement": "reps"})
    client.post(reverse("exercise_muscle_group", args=[shared.pk]), {"muscle_group_own": "Грудь"})
    client.post(reverse("exercise_equipment", args=[shared.pk]), {"equipment_own": "Штанга"})

    shared.refresh_from_db()
    assert shared.is_global
    assert shared.name == "Жим лёжа со штангой"
    assert shared.measurement == "reps"
    assert shared.equipment == "Штанга"


def test_admin_rename_to_taken_global_name_is_an_error(client, admin_user, shared):
    ExerciseFactory(name="Становая тяга", owner=None)
    client.force_login(admin_user)

    response = client.post(reverse("exercise_rename", args=[shared.pk]), {"name": "становая ТЯГА"})

    assert "уже есть" in response.content.decode()
    shared.refresh_from_db()
    assert shared.name == "Жим штанги лёжа"


def test_admin_cannot_edit_someone_elses_personal_exercise(client, admin_user, other_user):
    """Чужое личное — данные человека, а не справочник проекта: 404 и админу."""
    theirs = ExerciseFactory(name="Моё секретное", owner=other_user)
    client.force_login(admin_user)

    assert client.get(reverse("exercise_detail", args=[theirs.pk])).status_code == 404
    response = client.post(reverse("exercise_rename", args=[theirs.pk]), {"name": "Чужое"})
    assert response.status_code == 404
    assert client.post(reverse("exercise_delete", args=[theirs.pk])).status_code == 404


def test_admin_deletes_unused_global_exercise(client, admin_user, shared):
    client.force_login(admin_user)

    response = client.post(reverse("exercise_delete", args=[shared.pk]))

    assert response.status_code == 302
    assert not Exercise.objects.filter(pk=shared.pk).exists()


def test_global_exercise_used_by_anyone_is_not_deleted(client, admin_user, other_user, shared):
    """Тренировка любого пользователя держит общее упражнение — удаление блокируется."""
    StrengthSetFactory(workout=WorkoutFactory(user=other_user), exercise=shared)
    client.force_login(admin_user)

    client.post(reverse("exercise_delete", args=[shared.pk]))

    assert Exercise.objects.filter(pk=shared.pk).exists()


def test_exercise_page_budget_is_unchanged_for_admin(
    client, admin_user, shared, django_assert_max_num_queries
):
    client.force_login(admin_user)

    with django_assert_max_num_queries(9 + SIDEBAR_QUERIES):
        client.get(reverse("exercise_detail", args=[shared.pk]))
