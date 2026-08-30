"""Каталог упражнений, личные виды спорта и удаление записей справочников."""

import pytest
from django.urls import reverse

from workouts.models import Exercise, Sport
from workouts.tests.factories import (
    ExerciseFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


# ---------- Каталог упражнений ----------


def test_catalog_requires_login(client):
    response = client.get(reverse("exercise_list"))

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_catalog_shows_global_and_own_but_not_other_users(client, user, other_user):
    ExerciseFactory(name="Жим лёжа")
    ExerciseFactory(owner=user, name="Моё упражнение")
    ExerciseFactory(owner=other_user, name="Чужое упражнение")

    client.force_login(user)
    content = client.get(reverse("exercise_list")).content.decode()

    assert "Жим лёжа" in content
    assert "Моё упражнение" in content
    assert "Чужое упражнение" not in content


def test_catalog_mine_filter_keeps_only_own(client, user):
    ExerciseFactory(name="Жим лёжа")
    ExerciseFactory(owner=user, name="Моё упражнение")

    client.force_login(user)
    content = client.get(reverse("exercise_list"), {"mine": "1"}).content.decode()

    assert "Моё упражнение" in content
    assert "Жим лёжа" not in content


def test_catalog_search_filters_by_name(client, user):
    ExerciseFactory(name="Жим лёжа")
    ExerciseFactory(name="Присед со штангой")

    client.force_login(user)
    content = client.get(reverse("exercise_list"), {"q": "жим"}).content.decode()

    assert "Жим лёжа" in content
    assert "Присед со штангой" not in content


def test_catalog_shows_my_record_and_links_to_exercise(client, user):
    bench = ExerciseFactory(name="Жим лёжа")
    workout = WorkoutFactory(user=user)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1, weight_kg=80, reps=8)

    client.force_login(user)
    content = client.get(reverse("exercise_list")).content.decode()

    assert "80 кг" in content
    assert reverse("exercise_detail", args=[bench.pk]) in content


def test_catalog_shows_usage_label_for_own_exercise(client, user):
    mine = ExerciseFactory(owner=user, name="Моё упражнение")
    StrengthSetFactory(workout=WorkoutFactory(user=user), exercise=mine, set_number=1)

    client.force_login(user)
    content = client.get(reverse("exercise_list"), {"mine": "1"}).content.decode()

    assert "в 1 тренировке" in content


def test_catalog_shows_empty_states(client, user):
    client.force_login(user)

    mine = client.get(reverse("exercise_list"), {"mine": "1"}).content.decode()
    search = client.get(reverse("exercise_list"), {"q": "ничего"}).content.decode()

    assert "Своих упражнений пока нет" in mine
    assert "По этому запросу упражнений нет" in search


# ---------- Личные виды спорта ----------


def test_my_sports_lists_only_own_records(client, user, other_user):
    mine = SportFactory(owner=user, name="Кроссфит")
    SportFactory(name="Плавание")  # глобальный
    SportFactory(owner=other_user, name="Чужой спорт")

    client.force_login(user)
    response = client.get(reverse("my_sports"))

    content = response.content.decode()
    assert list(response.context["sports"]) == [mine]
    assert "Кроссфит" in content
    assert "Плавание" not in content
    assert "Чужой спорт" not in content


def test_my_sports_shows_usage_label(client, user):
    sport = SportFactory(owner=user, name="Кроссфит")
    WorkoutFactory(user=user, sport=sport)

    client.force_login(user)
    content = client.get(reverse("my_sports")).content.decode()

    assert "в 1 тренировке" in content


def test_my_sports_shows_empty_state(client, user):
    client.force_login(user)

    content = client.get(reverse("my_sports")).content.decode()

    assert "Своих видов спорта пока нет" in content


# ---------- Удаление ----------


def test_delete_page_renders_for_own_exercise(client, user):
    mine = ExerciseFactory(owner=user, name="Моё упражнение")

    client.force_login(user)
    content = client.get(reverse("exercise_delete", args=[mine.pk])).content.decode()

    assert "Удалить упражнение?" in content
    assert "Моё упражнение" in content


def test_delete_page_counts_workouts_not_sets(client, user):
    """Подпись говорит «в N тренировках» — значит и считать надо тренировки."""
    mine = ExerciseFactory(owner=user, name="Моё упражнение")
    workout = WorkoutFactory(user=user)
    for number in range(1, 5):
        StrengthSetFactory(workout=workout, exercise=mine, set_number=number)

    client.force_login(user)
    content = client.get(reverse("exercise_delete", args=[mine.pk])).content.decode()

    assert "в 1 тренировке" in content
    assert "в 4 тренировк" not in content


def test_delete_page_explains_why_used_record_is_kept(client, user):
    mine = ExerciseFactory(owner=user, name="Моё упражнение")
    StrengthSetFactory(workout=WorkoutFactory(user=user), exercise=mine, set_number=1)

    client.force_login(user)
    content = client.get(reverse("exercise_delete", args=[mine.pk])).content.decode()

    assert "нельзя удалить" in content
    assert "Удалить</button>" not in content


def test_counters_ignore_drafts(client, user):
    """Подпись «в N тренировках» считает записанные: черновик тренировкой не стал."""
    exercise = ExerciseFactory(owner=user, name="Моё упражнение")
    sport = SportFactory(owner=user, name="Кроссфит")
    planned = WorkoutFactory(user=user, sport=sport, started_at=None, duration_min=None)
    StrengthSetFactory(workout=planned, exercise=exercise, set_number=1, done=False)

    client.force_login(user)
    catalog = client.get(reverse("exercise_list"), {"mine": "1"}).content.decode()
    sports = client.get(reverse("my_sports")).content.decode()

    assert "не использовалось" in catalog
    assert "не использовалось" in sports


def test_draft_blocks_deletion_with_honest_message(client, user):
    """Черновик держит запись (FK PROTECT), но «записанной тренировкой» не является:
    иначе рядом с подписью «не использовалось» стояла бы падающая кнопка."""
    exercise = ExerciseFactory(owner=user, name="Моё упражнение")
    sport = SportFactory(owner=user, name="Кроссфит")
    planned = WorkoutFactory(user=user, sport=sport, started_at=None, duration_min=None)
    StrengthSetFactory(workout=planned, exercise=exercise, set_number=1, done=False)

    client.force_login(user)
    page = client.get(reverse("exercise_delete", args=[exercise.pk])).content.decode()
    response = client.post(reverse("sport_delete", args=[sport.pk]), follow=True)

    assert "есть в подготовленной тренировке" in page
    assert "Удалить</button>" not in page
    assert Sport.objects.filter(pk=sport.pk).exists()
    assert "сначала удалите черновик" in response.content.decode()


@pytest.mark.parametrize("owner", ["global", "other"], ids=["global", "other-user"])
def test_delete_page_404_for_foreign_exercise(client, user, other_user, owner):
    exercise = ExerciseFactory(owner=None if owner == "global" else other_user)

    client.force_login(user)

    assert client.get(reverse("exercise_delete", args=[exercise.pk])).status_code == 404


def test_get_does_not_delete(client, user):
    mine = ExerciseFactory(owner=user)

    client.force_login(user)
    client.get(reverse("exercise_delete", args=[mine.pk]))

    assert Exercise.objects.filter(pk=mine.pk).exists()


def test_unused_exercise_is_deleted(client, user):
    mine = ExerciseFactory(owner=user)

    client.force_login(user)
    response = client.post(reverse("exercise_delete", args=[mine.pk]))

    assert response.status_code == 302
    assert not Exercise.objects.filter(pk=mine.pk).exists()


def test_used_exercise_is_kept_with_message(client, user):
    """Использованную запись база защищает — и это не 500, а понятное сообщение."""
    mine = ExerciseFactory(owner=user, name="Моё упражнение")
    StrengthSetFactory(workout=WorkoutFactory(user=user), exercise=mine, set_number=1)

    client.force_login(user)
    response = client.post(reverse("exercise_delete", args=[mine.pk]), follow=True)

    assert response.status_code == 200
    assert Exercise.objects.filter(pk=mine.pk).exists()
    assert "нельзя удалить" in response.content.decode()


def test_unused_sport_is_deleted(client, user):
    mine = SportFactory(owner=user)

    client.force_login(user)
    response = client.post(reverse("sport_delete", args=[mine.pk]))

    assert response.status_code == 302
    assert not Sport.objects.filter(pk=mine.pk).exists()


def test_used_sport_is_kept_with_message(client, user):
    mine = SportFactory(owner=user, name="Кроссфит")
    WorkoutFactory(user=user, sport=mine)

    client.force_login(user)
    response = client.post(reverse("sport_delete", args=[mine.pk]), follow=True)

    assert response.status_code == 200
    assert Sport.objects.filter(pk=mine.pk).exists()
    assert "нельзя удалить" in response.content.decode()


def test_other_user_cannot_delete_my_exercise(client, user, other_user):
    mine = ExerciseFactory(owner=user)

    client.force_login(other_user)
    response = client.post(reverse("exercise_delete", args=[mine.pk]))

    assert response.status_code == 404
    assert Exercise.objects.filter(pk=mine.pk).exists()


def test_anonymous_cannot_delete_exercise(client, user):
    mine = ExerciseFactory(owner=user)

    response = client.post(reverse("exercise_delete", args=[mine.pk]))

    assert response.status_code == 302
    assert Exercise.objects.filter(pk=mine.pk).exists()
