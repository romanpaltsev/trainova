"""Каталог упражнений, личные виды спорта и удаление записей справочников."""

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.models import Exercise, Sport
from workouts.tests.factories import (
    ExerciseFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def days_ago(days):
    return timezone.now() - timedelta(days=days)


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


# ---------- Группы мышц: чипы, фильтр, заголовки ----------


def chip_links(html):
    """Адреса чипов-фильтров в порядке разметки: «Все», «Мои», затем группы."""
    block = html[html.index("app-chips app-filters") :]
    return [link.replace("&amp;", "&") for link in re.findall(r'href="([^"]*)"', block)]


def test_catalog_groups_exercises_by_muscle_group(client, user):
    """Без фильтра список разбит заголовками групп — иначе двадцать пять строк
    приходится читать вторыми строками, чтобы понять, что есть на спину."""
    client.force_login(user)
    ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")
    ExerciseFactory(name="Подтягивания", muscle_group="Спина")
    ExerciseFactory(name="Становая тяга", muscle_group="Спина")

    response = client.get(reverse("exercise_list"))

    groups = [
        (group["title"], [e.name for e in group["items"]]) for group in response.context["groups"]
    ]
    assert groups == [("Грудь", ["Жим лёжа"]), ("Спина", ["Подтягивания", "Становая тяга"])]
    assert response.context["shows_group_titles"] is True


def test_exercises_without_group_go_last(client, user):
    client.force_login(user)
    ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")
    ExerciseFactory(name="Загадочное упражнение", muscle_group="")

    titles = [group["title"] for group in client.get(reverse("exercise_list")).context["groups"]]

    assert titles == ["Грудь", "Без группы"]


def test_group_chips_come_from_data_and_hide_other_users(client, user, other_user):
    client.force_login(user)
    ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")
    ExerciseFactory(name="Каяк", muscle_group="Гребля", owner=other_user)

    chips = client.get(reverse("exercise_list")).context["muscle_groups"]

    assert chips == ["Грудь"]


def test_group_filter_narrows_the_list(client, user):
    client.force_login(user)
    ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")
    ExerciseFactory(name="Подтягивания", muscle_group="Спина")

    response = client.get(reverse("exercise_list"), {"group": "Спина"})

    assert [e.name for e in response.context["exercises"]] == ["Подтягивания"]
    assert response.context["group_filter"] == "Спина"
    # Заголовок группы не нужен: её название уже стоит в активном чипе.
    assert response.context["shows_group_titles"] is False


def test_group_filter_is_case_insensitive_but_keeps_stored_spelling(client, user):
    client.force_login(user)
    ExerciseFactory(name="Подтягивания", muscle_group="Спина")

    response = client.get(reverse("exercise_list"), {"group": "спина"})

    assert response.context["group_filter"] == "Спина"
    assert len(response.context["exercises"]) == 1


def test_unknown_group_is_ignored_not_404(client, user):
    """Чипы приходят из данных и в открытой вкладке могли устареть."""
    client.force_login(user)
    ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")

    response = client.get(reverse("exercise_list"), {"group": "Хвост"})

    assert response.status_code == 200
    assert response.context["group_filter"] == ""
    assert len(response.context["exercises"]) == 1


def test_group_filter_combines_with_mine_and_search(client, user):
    client.force_login(user)
    ExerciseFactory(name="Присед с гантелями", muscle_group="Ноги", owner=user)
    ExerciseFactory(name="Присед со штангой", muscle_group="Ноги")
    ExerciseFactory(name="Жим ногами", muscle_group="Ноги", owner=user)

    response = client.get(reverse("exercise_list"), {"group": "Ноги", "mine": "1", "q": "присед"})

    assert [e.name for e in response.context["exercises"]] == ["Присед с гантелями"]


def test_group_chip_keeps_search_and_owner(client, user):
    """Чип группы не должен сбрасывать поиск, а адрес обязан быть абсолютным: на
    десктопе после выбора упражнения адрес страницы — /exercises/5/, и
    относительный «?group=…» увёл бы на деталь."""
    client.force_login(user)
    ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")

    html = client.get(reverse("exercise_list"), {"mine": "1", "q": "жим"}).content.decode()

    chip = chip_links(html)[2]  # 0 — «Все», 1 — «Мои», дальше группы
    assert chip.startswith(reverse("exercise_list"))
    assert "mine=1" in chip and "q=%D0%B6%D0%B8%D0%BC" in chip
    assert "group=%D0%93%D1%80%D1%83%D0%B4%D1%8C" in chip


def test_active_group_chip_removes_the_filter(client, user):
    """Повторный тап по активному чипу снимает фильтр — иначе из группы не выйти."""
    client.force_login(user)
    ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")

    html = client.get(reverse("exercise_list"), {"group": "Грудь"}).content.decode()

    assert "group=" not in chip_links(html)[2]


def test_dense_rows_are_scoped_to_the_catalog(client, user):
    """Плотные строки — вариант каталога. В профиле и «Моих видах спорта» строки
    остаются отдельными карточками, и общий .app-row трогать нельзя."""
    client.force_login(user)
    ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")

    catalog = client.get(reverse("exercise_list")).content.decode()
    profile = client.get(reverse("profile")).content.decode()
    sports = client.get(reverse("my_sports")).content.decode()
    locations = client.get(reverse("my_locations")).content.decode()

    assert "app-rows-dense" in catalog
    assert "app-rows-dense" not in profile
    assert "app-rows-dense" not in sports
    assert "app-rows-dense" not in locations


# ---------- Блок «Я тренирую» ----------
#
# Каталог делится на два блока: сверху упражнения, которые пользователь
# действительно делал, ниже — полный справочник по группам мышц. Признак деления —
# записанная тренировка, а не наличие рекорда: у подтягиваний с собственным весом
# и у упражнения со сменённой единицей рекорда нет, а тренировок двадцать.


def trained_names(response):
    return [exercise.name for exercise in response.context["trained"]]


def test_trained_block_shows_only_recorded_exercises(client, user):
    done = ExerciseFactory(name="Жим лёжа")
    never = ExerciseFactory(name="Планка")
    drafted = ExerciseFactory(name="Скручивания")
    StrengthSetFactory(workout__user=user, exercise=done, set_number=1, weight_kg=80, reps=8)
    # Черновик тренировкой ещё не стал: duration_min пуст.
    StrengthSetFactory(
        workout__user=user,
        workout__duration_min=None,
        exercise=drafted,
        set_number=1,
        done=False,
    )

    client.force_login(user)
    response = client.get(reverse("exercise_list"))

    assert trained_names(response) == ["Жим лёжа"]
    assert never.name not in trained_names(response)
    assert drafted.name not in trained_names(response)


def test_trained_block_is_ordered_by_last_workout(client, user):
    """Свежее сверху; внутри одной тренировки — по числу тренировок, потом по имени."""
    older = ExerciseFactory(name="Становая тяга")
    recent_a = ExerciseFactory(name="Бжим")
    recent_b = ExerciseFactory(name="Ажим")
    old_workout = WorkoutFactory(user=user, started_at=days_ago(10))
    StrengthSetFactory(workout=old_workout, exercise=older, set_number=1, weight_kg=100, reps=5)
    # Оба в одной тренировке: last_workout_at совпадает, решает тайбрейк.
    fresh = WorkoutFactory(user=user, started_at=days_ago(1))
    StrengthSetFactory(workout=fresh, exercise=recent_a, set_number=1, weight_kg=70, reps=8)
    StrengthSetFactory(workout=fresh, exercise=recent_b, set_number=2, weight_kg=70, reps=8)
    # У «Бжим» вторая тренировка — по числу он выше «Ажим», хотя по алфавиту ниже.
    second = WorkoutFactory(user=user, started_at=days_ago(1))
    StrengthSetFactory(workout=second, exercise=recent_a, set_number=1, weight_kg=70, reps=8)

    client.force_login(user)
    response = client.get(reverse("exercise_list"))

    assert trained_names(response) == ["Бжим", "Ажим", "Становая тяга"]


def test_other_users_workouts_do_not_make_exercise_mine(client, user, other_user):
    """Изоляция: чужая тренировка на глобальном упражнении не делает его моим."""
    shared = ExerciseFactory(name="Жим лёжа", owner=None)
    StrengthSetFactory(workout__user=other_user, exercise=shared, set_number=1, weight_kg=200)

    client.force_login(user)
    response = client.get(reverse("exercise_list"))

    assert trained_names(response) == []


@pytest.mark.parametrize(
    "params",
    [{"q": "жим"}, {"mine": "1"}, {"group": "Грудь"}],
    ids=["search", "mine", "group"],
)
def test_filters_hide_the_trained_block(client, user, params):
    """При поиске нужен один список результатов, а не два места, по которым они разбросаны."""
    bench = ExerciseFactory(name="Жим лёжа", muscle_group="Грудь", owner=user)
    StrengthSetFactory(workout__user=user, exercise=bench, set_number=1, weight_kg=80, reps=8)

    client.force_login(user)
    response = client.get(reverse("exercise_list"), params)

    assert response.context["trained"] == []
    assert response.context["shows_blocks"] is False
    assert "Я тренирую" not in response.content.decode()
    # Само упражнение при этом на экране есть — оно в списке.
    assert bench.name in response.content.decode()


def test_trained_exercise_stays_in_the_reference_list(client, user):
    """Справочник остаётся полным: в «Груди» должно быть всё про грудь."""
    bench = ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")
    StrengthSetFactory(workout__user=user, exercise=bench, set_number=1, weight_kg=80, reps=8)

    client.force_login(user)
    response = client.get(reverse("exercise_list"))

    assert trained_names(response) == ["Жим лёжа"]
    in_groups = [item.name for group in response.context["groups"] for item in group["items"]]
    assert "Жим лёжа" in in_groups


def test_trained_exercise_without_record_stays_in_the_block(client, user):
    """Подтягивания с собственным весом: тренировки есть, рекорда нет."""
    pullups = ExerciseFactory(name="Подтягивания")
    StrengthSetFactory(workout__user=user, exercise=pullups, set_number=1, weight_kg=0, reps=12)

    client.force_login(user)
    response = client.get(reverse("exercise_list"))

    assert trained_names(response) == ["Подтягивания"]
    assert response.context["trained"][0].record_display is None


def test_user_without_workouts_sees_only_the_reference_list(client, user):
    ExerciseFactory(name="Жим лёжа")

    client.force_login(user)
    response = client.get(reverse("exercise_list"))

    assert response.context["trained"] == []
    assert response.context["shows_blocks"] is False
    assert "Жим лёжа" in response.content.decode()


def test_group_titles_show_when_no_group_chip_is_active(client, user):
    """Единственная группа без фильтра остаётся безымянной, если не назвать её."""
    ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")

    client.force_login(user)
    without_filter = client.get(reverse("exercise_list"))
    with_filter = client.get(reverse("exercise_list"), {"group": "Грудь"})

    assert without_filter.context["shows_group_titles"] is True
    # При активном чипе название группы уже стоит в нём.
    assert with_filter.context["shows_group_titles"] is False
