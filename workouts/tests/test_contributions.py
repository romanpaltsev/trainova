"""Вклад в общий справочник: «Сделать общим» администратора и авторство.

Запись не копируется, а меняет владельца — история автора остаётся на ней.
Общая с тем же названием мешает переводу. Автора общей записи видят только он
сам и администратор, остальные — «участник» без имени.
"""

import pytest
from django.urls import reverse

from workouts import contributions
from workouts.models import Exercise, ExerciseSettings, MachineBrand, MachineModel
from workouts.tests.factories import (
    ExerciseFactory,
    ExerciseMachineFactory,
    ExerciseSettingsFactory,
    MachineBrandFactory,
    MachineModelFactory,
    StrengthSetFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user(user):
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    return user


def test_make_global_keeps_history_and_settings(user):
    exercise = ExerciseFactory(name="Жим Смита", owner=user, muscle_group="грудь")
    ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")
    done = StrengthSetFactory(exercise=exercise, workout__user=user)
    ExerciseSettingsFactory(user=user, exercise=exercise)

    contributions.make_global(exercise, contributor=user)

    exercise.refresh_from_db()
    assert exercise.is_global
    assert exercise.contributed_by == user
    assert exercise.muscle_group == "Грудь"  # написание общих
    done.refresh_from_db()
    assert done.exercise_id == exercise.pk
    assert ExerciseSettings.objects.filter(user=user, exercise=exercise).exists()


def test_global_twin_blocks(user):
    exercise = ExerciseFactory(name="жим лёжа", owner=user)
    twin = ExerciseFactory(name="Жим лёжа")

    with pytest.raises(contributions.DuplicateError) as error:
        contributions.make_global(exercise)

    assert error.value.existing == twin
    exercise.refresh_from_db()
    assert not exercise.is_global


def test_model_of_own_brand_takes_brand_along(user):
    model = MachineModelFactory(
        brand=MachineBrandFactory(name="Kettler", owner=user), name="Axos", owner=user
    )
    other_model = MachineModelFactory(brand=model.brand, name="Своя", owner=user)

    published = contributions.make_global(model, contributor=user)

    model.refresh_from_db()
    model.brand.refresh_from_db()
    assert published == [model.brand, model]
    assert model.is_global and model.brand.is_global
    assert model.brand.contributed_by == user
    other_model.refresh_from_db()
    assert not other_model.is_global  # своя модель у общего производителя — законно


def test_brand_twin_blocks_model(user):
    MachineBrandFactory(name="Kettler")
    model = MachineModelFactory(
        brand=MachineBrandFactory(name="KETTLER", owner=user), name="Axos", owner=user
    )

    with pytest.raises(contributions.DuplicateError):
        contributions.make_global(model)

    assert not MachineModel.objects.get(pk=model.pk).is_global
    assert MachineBrand.objects.global_only().count() == 1


def test_model_twin_within_brand_blocks(user):
    shared = MachineBrandFactory(name="Technogym")
    MachineModelFactory(brand=shared, name="Pure")
    mine = MachineModelFactory(brand=shared, name="pure", owner=user)

    with pytest.raises(contributions.DuplicateError):
        contributions.make_global(mine)


def test_admin_makes_own_exercise_global(client, admin_user):
    exercise = ExerciseFactory(name="Жим Смита", owner=admin_user)
    client.force_login(admin_user)
    url = reverse("exercise_make_global", args=[exercise.pk])

    assert "станет общим" in client.get(url).content.decode()
    response = client.post(url)

    assert response.headers["HX-Refresh"] == "true"
    exercise.refresh_from_db()
    assert exercise.is_global
    assert exercise.contributed_by is None  # работа админа, не заявка


def test_admin_duplicate_shows_error(client, admin_user):
    exercise = ExerciseFactory(name="Жим лёжа", owner=admin_user)
    twin = ExerciseFactory(name="Жим лёжа")
    client.force_login(admin_user)

    html = client.post(reverse("exercise_make_global", args=[exercise.pk])).content.decode()

    assert "Общее упражнение «Жим лёжа» уже есть." in html
    assert reverse("exercise_detail", args=[twin.pk]) in html


def test_make_global_is_404_for_regular_user_and_foreign(client, user, admin_user, other_user):
    # admin_user и user — один и тот же человек; нужен отдельный обычный.
    theirs = ExerciseFactory(name="Чужое", owner=other_user)
    shared = ExerciseFactory(name="Общее")
    client.force_login(admin_user)
    for exercise in (theirs, shared):
        assert client.post(reverse("exercise_make_global", args=[exercise.pk])).status_code == 404

    client.force_login(other_user)
    assert client.post(reverse("exercise_make_global", args=[theirs.pk])).status_code == 404
    brand = MachineBrandFactory(name="Kettler", owner=other_user)
    url = reverse("machine_make_global", args=["brand", brand.pk])
    assert client.post(url).status_code == 404
    assert not Exercise.objects.get(pk=theirs.pk).is_global


def test_admin_makes_machine_global(client, admin_user):
    machine = ExerciseMachineFactory(
        user=admin_user,
        brand=MachineBrandFactory(name="Kettler", owner=admin_user),
        model=MachineModelFactory(
            brand=MachineBrandFactory(name="Kettler", owner=admin_user),
            name="Axos",
            owner=admin_user,
        ),
    )
    client.force_login(admin_user)

    assert (
        reverse("machine_make_global", args=["model", machine.model_id])
        in client.get(reverse("my_machines")).content.decode()
    )
    client.post(reverse("machine_make_global", args=["model", machine.model_id]))

    machine.refresh_from_db()
    assert machine.brand.is_global and machine.model.is_global


def test_authorship_line(client, user, other_user):
    exercise = ExerciseFactory(name="Жим Смита", owner=user)
    contributions.make_global(exercise, contributor=user)
    url = reverse("exercise_detail", args=[exercise.pk])

    client.force_login(user)
    assert "Вы добавили это упражнение" in client.get(url).content.decode()

    client.force_login(other_user)
    html = client.get(url).content.decode()
    assert "Добавлено участником." in html
    assert user.email not in html

    other_user.is_staff = True
    other_user.save(update_fields=["is_staff"])
    assert f"Добавлено участником: {user.email}" in client.get(url).content.decode()


def test_author_account_deletion_keeps_global(user):
    from accounts.deletion import delete_user_data

    exercise = ExerciseFactory(name="Жим Смита", owner=user)
    contributions.make_global(exercise, contributor=user)

    delete_user_data(user)

    exercise.refresh_from_db()
    assert exercise.is_global and exercise.contributed_by is None
