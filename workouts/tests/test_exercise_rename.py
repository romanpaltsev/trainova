"""Переименование своего упражнения и снаряд на его странице.

Переименование появилось по той же причине, что у мест: опечатка правится один
раз и чинится по всей истории. Это та же строка БД, поэтому подходы, рекорд и
график остаются на месте — глобальные упражнения так не правятся, их имя меняет
миграция.
"""

import pytest
from django.urls import reverse

from workouts.models import Exercise, StrengthSet
from workouts.tests.factories import ExerciseFactory, StrengthSetFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


def rename(client, exercise, **payload):
    return client.post(reverse("exercise_rename", args=[exercise.pk]), payload)


def set_equipment(client, exercise, **payload):
    return client.post(reverse("exercise_equipment", args=[exercise.pk]), payload)


def with_history(user, exercise):
    workout = WorkoutFactory(user=user)
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1)
    return workout


# ---------- Переименование ----------


def test_own_exercise_is_renamed_and_history_survives(client, user):
    mine = ExerciseFactory(name="жим гантелей лежа", owner=user)
    with_history(user, mine)

    client.force_login(user)
    response = rename(client, mine, name="  Жим   гантелей лёжа ")

    assert response.status_code == 200
    mine.refresh_from_db()
    # Лишние пробелы схлопываются — то же правило, что у мест и упражнений.
    assert mine.name == "Жим гантелей лёжа"
    assert StrengthSet.objects.filter(exercise=mine).count() == 1


def test_rename_answers_with_the_page_body(client, user):
    """Ответ свапает сам себя (OOB), поэтому модалка закрывается пустым остатком."""
    mine = ExerciseFactory(name="Моё", owner=user)

    client.force_login(user)
    content = rename(client, mine, name="Новое имя").content.decode()

    assert 'id="exercise-body"' in content
    assert 'hx-swap-oob="outerHTML"' in content
    assert "Новое имя" in content


def test_rename_keeps_the_head_of_the_screen_it_came_from(client, user):
    """Запрос из модалки всегда HTMX: режим приезжает полем формы, а не заголовком."""
    mine = ExerciseFactory(name="Моё", owner=user)

    client.force_login(user)
    page = rename(client, mine, name="Со страницы").content.decode()
    panel = rename(client, mine, name="Из панели", panel="1").content.decode()

    # На отдельной странице шапка со стрелкой «Назад», в панели её быть не должно.
    assert 'aria-label="Назад"' in page
    assert 'aria-label="Назад"' not in panel


def test_empty_name_is_rejected(client, user):
    mine = ExerciseFactory(name="Моё", owner=user)

    client.force_login(user)
    response = rename(client, mine, name="   ")

    mine.refresh_from_db()
    assert mine.name == "Моё"
    assert "Введите название." in response.content.decode()


def test_name_taken_by_a_global_exercise_is_rejected(client, user):
    """Иначе в списке оказались бы две одинаковые строки, и различить их нечем."""
    ExerciseFactory(name="Жим штанги лёжа", owner=None)
    mine = ExerciseFactory(name="Моё", owner=user)

    client.force_login(user)
    response = rename(client, mine, name="жим штанги лёжа")

    mine.refresh_from_db()
    assert mine.name == "Моё"
    assert "уже есть" in response.content.decode()


def test_own_other_name_is_taken_too(client, user):
    ExerciseFactory(name="Жим гантелей", owner=user)
    mine = ExerciseFactory(name="Моё", owner=user)

    client.force_login(user)
    rename(client, mine, name="Жим гантелей")

    mine.refresh_from_db()
    assert mine.name == "Моё"


def test_same_name_in_another_spelling_is_allowed(client, user):
    """Переименование в себя же — это правка написания, а не занятое имя."""
    mine = ExerciseFactory(name="жим гантелей", owner=user)

    client.force_login(user)
    rename(client, mine, name="Жим гантелей")

    mine.refresh_from_db()
    assert mine.name == "Жим гантелей"


def test_long_name_is_cut_to_the_column(client, user):
    mine = ExerciseFactory(name="Моё", owner=user)

    client.force_login(user)
    rename(client, mine, name="Ж" * 200)

    mine.refresh_from_db()
    assert len(mine.name) == 80


def test_foreign_and_global_exercises_cannot_be_renamed(client, user, other_user):
    """Священное правило: чужое по прямому URL — 404, глобальное правит админ."""
    alien = ExerciseFactory(name="Чужое", owner=other_user)
    globalny = ExerciseFactory(name="Жим штанги лёжа", owner=None)

    client.force_login(user)

    assert rename(client, alien, name="Моё").status_code == 404
    assert rename(client, globalny, name="Моё").status_code == 404
    assert client.get(reverse("exercise_rename", args=[globalny.pk])).status_code == 404
    alien.refresh_from_db()
    globalny.refresh_from_db()
    assert (alien.name, globalny.name) == ("Чужое", "Жим штанги лёжа")


def test_rename_button_is_shown_only_on_own_exercises(client, user):
    mine = ExerciseFactory(name="Моё", owner=user)
    globalny = ExerciseFactory(name="Жим штанги лёжа", owner=None)

    client.force_login(user)
    own_page = client.get(reverse("exercise_detail", args=[mine.pk])).content.decode()
    global_page = client.get(reverse("exercise_detail", args=[globalny.pk])).content.decode()

    assert reverse("exercise_rename", args=[mine.pk]) in own_page
    assert reverse("exercise_rename", args=[globalny.pk]) not in global_page


# ---------- Снаряд на странице упражнения ----------


def test_own_exercise_equipment_is_editable(client, user):
    ExerciseFactory(name="Жим штанги лёжа", owner=None, equipment="Штанга")
    mine = ExerciseFactory(name="Моё", owner=user, equipment="")

    client.force_login(user)
    response = set_equipment(client, mine, equipment="Штанга")

    assert response.status_code == 200
    mine.refresh_from_db()
    assert mine.equipment == "Штанга"
    assert "Сохранено." in response.content.decode()


def test_own_equipment_can_be_typed_and_cleared(client, user):
    mine = ExerciseFactory(name="Моё", owner=user, equipment="Штанга")

    client.force_login(user)
    set_equipment(client, mine, equipment_own="Резина")
    mine.refresh_from_db()
    assert mine.equipment == "Резина"

    response = set_equipment(client, mine)
    mine.refresh_from_db()
    assert mine.equipment == ""
    assert "Снаряд убран." in response.content.decode()


def test_own_equipment_is_normalized_to_existing(client, user):
    """Иначе в чипах появились бы «Гантели» и «гантели» — то же правило, что у групп."""
    ExerciseFactory(name="Жим гантелей лёжа", owner=None, equipment="Гантели")
    mine = ExerciseFactory(name="Моё", owner=user, equipment="")

    client.force_login(user)
    set_equipment(client, mine, equipment_own="  гантели ")

    mine.refresh_from_db()
    assert mine.equipment == "Гантели"


def test_own_equipment_field_beats_the_chip(client, user):
    ExerciseFactory(name="Жим штанги лёжа", owner=None, equipment="Штанга")
    mine = ExerciseFactory(name="Моё", owner=user, equipment="")

    client.force_login(user)
    set_equipment(client, mine, equipment="Штанга", equipment_own="Гиря")

    mine.refresh_from_db()
    assert mine.equipment == "Гиря"


def test_global_exercise_equipment_is_shown_but_not_editable(client, user):
    globalny = ExerciseFactory(name="Жим штанги лёжа", owner=None, equipment="Штанга")

    client.force_login(user)
    page = client.get(reverse("exercise_detail", args=[globalny.pk])).content.decode()
    saved = set_equipment(client, globalny, equipment_own="Гиря")

    assert "Снаряд" in page
    assert reverse("exercise_equipment", args=[globalny.pk]) not in page
    assert saved.status_code == 404
    globalny.refresh_from_db()
    assert globalny.equipment == "Штанга"


def test_foreign_exercise_equipment_is_untouchable(client, user, other_user):
    alien = ExerciseFactory(name="Чужое", owner=other_user, equipment="Штанга")

    client.force_login(user)

    assert set_equipment(client, alien, equipment_own="Гиря").status_code == 404
    alien.refresh_from_db()
    assert alien.equipment == "Штанга"


def test_equipment_chips_hide_other_users(client, user, other_user):
    ExerciseFactory(name="Жим штанги лёжа", owner=None, equipment="Штанга")
    ExerciseFactory(name="Чужое", owner=other_user, equipment="Каяк")
    mine = ExerciseFactory(name="Моё", owner=user, equipment="")

    client.force_login(user)
    response = client.get(reverse("exercise_detail", args=[mine.pk]))

    assert response.context["equipment_list"] == ["Штанга"]
    assert "Каяк" not in response.content.decode()


def test_new_equipment_appears_among_the_chips_right_away(client, user):
    """Список спрашивается после сохранения: иначе своё значение пропало бы из выбора."""
    mine = ExerciseFactory(name="Моё", owner=user, equipment="")

    client.force_login(user)
    content = set_equipment(client, mine, equipment_own="Резина").content.decode()

    assert content.count("Резина") >= 2  # чип и значение поля


def test_exercise_page_shows_both_facets(client, user):
    mine = ExerciseFactory(name="Моё", owner=user, muscle_group="Грудь", equipment="Штанга")

    client.force_login(user)
    content = client.get(reverse("exercise_detail", args=[mine.pk])).content.decode()

    assert "Группа мышц" in content
    assert "Снаряд" in content
    assert reverse("exercise_equipment", args=[mine.pk]) in content


def test_equipment_page_queries_stay_in_budget(client, user, django_assert_max_num_queries):
    """Вторая ось приехала вместе с первой — бюджет страницы не двинулся."""
    mine = ExerciseFactory(name="Моё", owner=user, equipment="Штанга")
    with_history(user, mine)

    client.force_login(user)
    with django_assert_max_num_queries(9):
        client.get(reverse("exercise_detail", args=[mine.pk]))


def test_rename_does_not_touch_other_exercises(client, user):
    mine = ExerciseFactory(name="Моё", owner=user)
    other = ExerciseFactory(name="Другое моё", owner=user)

    client.force_login(user)
    rename(client, mine, name="Переименованное")

    other.refresh_from_db()
    assert other.name == "Другое моё"
    assert Exercise.objects.filter(owner=user).count() == 2
