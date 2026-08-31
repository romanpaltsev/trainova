"""Группа мышц у своих упражнений: чипы уже принятых значений плюс своя.

Поле `Exercise.muscle_group` было с самого начала, но заполнялось только сидом и
админкой — у созданных пользователем упражнений группа всегда оставалась пустой.
"""

import pytest
from django.urls import reverse

from workouts.models import Exercise, muscle_groups_for, normalize_muscle_group
from workouts.tests.factories import ExerciseFactory, WorkoutFactory

pytestmark = pytest.mark.django_db

MEASURE = Exercise.Measurement


def active(user):
    return WorkoutFactory(user=user, duration_min=None)


def create_exercise(client, workout, name, **extra):
    return client.post(reverse("live_exercises", args=[workout.pk]), {"name": name, **extra})


def set_group(client, exercise, **payload):
    return client.post(reverse("exercise_muscle_group", args=[exercise.pk]), payload)


# ---------- Список групп ----------


def test_groups_come_from_visible_exercises_only(user, other_user):
    """Священное правило: чужая личная группа в подсказках не появляется."""
    ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")
    ExerciseFactory(name="Моё", owner=user, muscle_group="Предплечья")
    ExerciseFactory(name="Чужое", owner=other_user, muscle_group="Чужая группа")
    ExerciseFactory(name="Без группы", owner=user, muscle_group="")

    assert muscle_groups_for(user) == ["Грудь", "Предплечья"]


def test_normalization_keeps_the_accepted_spelling():
    known = ["Грудь", "Ноги"]

    assert normalize_muscle_group("  грудь ", known) == "Грудь"
    assert normalize_muscle_group("ГРУДЬ", known) == "Грудь"
    # Новая группа остаётся как написали, лишние пробелы схлопываются.
    assert normalize_muscle_group("  задняя   дельта ", known) == "задняя дельта"


# ---------- Создание упражнения ----------


def test_created_exercise_takes_the_chosen_group(client, user):
    ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")
    workout = active(user)

    client.force_login(user)
    create_exercise(client, workout, "Разводка в кроссовере", muscle_group="Грудь")

    created = Exercise.objects.get(name="Разводка в кроссовере")
    assert created.owner == user
    assert created.muscle_group == "Грудь"


def test_own_group_beats_the_chip(client, user):
    """Своё поле заполняют, когда среди чипов нужного нет — оно и должно выиграть."""
    ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")
    workout = active(user)

    client.force_login(user)
    create_exercise(
        client,
        workout,
        "Обратные разведения",
        muscle_group="Грудь",
        muscle_group_own="Задняя дельта",
    )

    assert Exercise.objects.get(name="Обратные разведения").muscle_group == "Задняя дельта"


def test_own_group_is_normalized_to_existing(client, user):
    ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")
    workout = active(user)

    client.force_login(user)
    create_exercise(client, workout, "Пуловер", muscle_group_own="грудь")

    assert Exercise.objects.get(name="Пуловер").muscle_group == "Грудь"


def test_group_is_optional(client, user):
    workout = active(user)

    client.force_login(user)
    response = create_exercise(client, workout, "Без группы")

    assert response.status_code == 200
    assert Exercise.objects.get(name="Без группы").muscle_group == ""


def test_existing_name_keeps_its_group(client, user):
    """Ввод названия существующего упражнения добавляет его, а не переопределяет."""
    existing = ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")
    workout = active(user)

    client.force_login(user)
    create_exercise(client, workout, "Жим лёжа", muscle_group_own="Ноги")

    existing.refresh_from_db()
    assert existing.muscle_group == "Грудь"
    assert Exercise.objects.filter(name__iexact="Жим лёжа").count() == 1


def test_chips_are_offered_only_with_the_create_option(client, user):
    ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")
    workout = active(user)

    client.force_login(user)
    with_offer = client.get(reverse("live_exercises", args=[workout.pk]), {"q": "Совсем новое"})
    without_offer = client.get(reverse("live_exercises", args=[workout.pk]), {"q": "Жим лёжа"})

    assert with_offer.context["muscle_groups"] == ["Грудь"]
    # Список групп на поиске не нужен — и лишнего запроса на каждую букву нет.
    assert without_offer.context["muscle_groups"] == []


def test_chosen_group_survives_the_next_letter(client, user):
    """Чипы живут в свапаемом блоке результатов, поэтому выбор ездит на сервер."""
    ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")
    workout = active(user)

    client.force_login(user)
    response = client.get(
        reverse("live_exercises", args=[workout.pk]),
        {"q": "Пуловер", "muscle_group": "Грудь"},
    )

    assert response.context["selected_muscle_group"] == "Грудь"


# ---------- Правка на странице упражнения ----------


def test_own_exercise_group_is_editable(client, user):
    ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")
    mine = ExerciseFactory(name="Моё упражнение", owner=user, muscle_group="")

    client.force_login(user)
    response = set_group(client, mine, muscle_group="Грудь")

    assert response.status_code == 200
    mine.refresh_from_db()
    assert mine.muscle_group == "Грудь"
    assert "Сохранено." in response.content.decode()


def test_own_group_can_be_typed_and_cleared(client, user):
    mine = ExerciseFactory(name="Моё упражнение", owner=user, muscle_group="Грудь")

    client.force_login(user)
    set_group(client, mine, muscle_group_own="Задняя дельта")
    mine.refresh_from_db()
    assert mine.muscle_group == "Задняя дельта"

    response = set_group(client, mine)
    mine.refresh_from_db()
    assert mine.muscle_group == ""
    assert "Группа убрана." in response.content.decode()


def test_exercise_page_shows_the_block(client, user):
    ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")
    mine = ExerciseFactory(name="Моё упражнение", owner=user, muscle_group="Грудь")

    client.force_login(user)
    content = client.get(reverse("exercise_detail", args=[mine.pk])).content.decode()

    assert "Группа мышц" in content
    assert reverse("exercise_muscle_group", args=[mine.pk]) in content


def test_global_exercise_group_is_shown_but_not_editable(client, user):
    """Глобальные справочники правит только админ."""
    globalny = ExerciseFactory(name="Жим лёжа", owner=None, muscle_group="Грудь")

    client.force_login(user)
    page = client.get(reverse("exercise_detail", args=[globalny.pk])).content.decode()
    saved = set_group(client, globalny, muscle_group_own="Ноги")

    assert "Грудь" in page
    assert reverse("exercise_muscle_group", args=[globalny.pk]) not in page
    assert saved.status_code == 404
    globalny.refresh_from_db()
    assert globalny.muscle_group == "Грудь"


def test_foreign_exercise_group_is_untouchable(client, user, other_user):
    alien = ExerciseFactory(name="Чужое", owner=other_user, muscle_group="Грудь")

    client.force_login(user)

    assert set_group(client, alien, muscle_group_own="Ноги").status_code == 404
    alien.refresh_from_db()
    assert alien.muscle_group == "Грудь"


def test_catalog_row_shows_the_group(client, user):
    ExerciseFactory(name="Моё упражнение", owner=user, muscle_group="Задняя дельта")

    client.force_login(user)
    content = client.get(reverse("exercise_list"), {"mine": "1"}).content.decode()

    assert "Задняя дельта" in content
