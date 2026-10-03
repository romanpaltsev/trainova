"""Таблица справочника упражнений на ПК: сортировка колонок.

Таблица — те же упражнения, что и телефонные раскладки, только в порядке
выбранной колонки. Сортировка считается в Python по уже загруженному списку,
поэтому телефон (группы и плитки) от параметра sort не меняется.
"""

import re
from datetime import date, datetime, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.tests.factories import ExerciseFactory, StrengthSetFactory, WorkoutFactory
from workouts.views import last_workout_label, parse_catalog_sort

pytestmark = pytest.mark.django_db


def days_ago(days):
    return timezone.now() - timedelta(days=days)


def train(user, exercise, days, times=1):
    for _ in range(times):
        workout = WorkoutFactory(user=user, started_at=days_ago(days))
        StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, weight_kg=70, reps=8)


def table_names(client, **params):
    response = client.get(reverse("exercise_list"), params)
    assert response.status_code == 200
    return [exercise.name for exercise in response.context["table_rows"]]


@pytest.fixture
def catalog(user):
    """Четыре тренированных и два нетренированных упражнения в разных группах.

    Порядок по дате с тайбрейками — как в тесте плиток «Я тренирую»: «Бжим» и
    «Ажим» из одной свежей тренировки, у «Бжим» тренировок больше.
    """
    older = ExerciseFactory(name="Становая тяга", muscle_group="Спина")
    recent_a = ExerciseFactory(name="Бжим", muscle_group="Грудь")
    recent_b = ExerciseFactory(name="Ажим", muscle_group="Грудь")
    legs = ExerciseFactory(name="Присед", muscle_group="Ноги")
    ExerciseFactory(name="Планка", muscle_group="Пресс")
    ExerciseFactory(name="Гиперэкстензия", muscle_group="")
    old_workout = WorkoutFactory(user=user, started_at=days_ago(10))
    StrengthSetFactory(workout=old_workout, exercise=older, set_number=1, weight_kg=100, reps=5)
    fresh = WorkoutFactory(user=user, started_at=days_ago(1))
    StrengthSetFactory(workout=fresh, exercise=recent_a, set_number=1, weight_kg=70, reps=8)
    StrengthSetFactory(workout=fresh, exercise=recent_b, set_number=2, weight_kg=70, reps=8)
    train(user, recent_a, days=1)
    train(user, legs, days=20, times=3)


def test_default_order_is_last_workout_first(client, user, catalog):
    """Свежее сверху, внутри одной тренировки — кто чаще; нетренированные — по алфавиту."""
    client.force_login(user)

    assert table_names(client) == [
        "Бжим",
        "Ажим",
        "Становая тяга",
        "Присед",
        "Гиперэкстензия",
        "Планка",
    ]


def test_sort_by_name_both_directions(client, user, catalog):
    client.force_login(user)

    names = table_names(client, sort="name")

    assert names == sorted(names)
    assert table_names(client, sort="-name") == names[::-1]


def test_sort_by_group_keeps_ungrouped_last(client, user, catalog):
    """Без группы — в хвосте при любом направлении, внутри группы — по названию."""
    client.force_login(user)

    ascending = table_names(client, sort="group")
    descending = table_names(client, sort="-group")

    assert ascending == ["Ажим", "Бжим", "Присед", "Планка", "Становая тяга", "Гиперэкстензия"]
    assert descending == ["Становая тяга", "Планка", "Присед", "Ажим", "Бжим", "Гиперэкстензия"]


def test_sort_by_workouts_keeps_untrained_last(client, user, catalog):
    client.force_login(user)

    most_first = table_names(client, sort="-workouts")
    fewest_first = table_names(client, sort="workouts")

    assert most_first[:4] == ["Присед", "Бжим", "Ажим", "Становая тяга"]
    assert fewest_first[:4] == ["Становая тяга", "Ажим", "Бжим", "Присед"]
    assert most_first[4:] == fewest_first[4:] == ["Гиперэкстензия", "Планка"]


def test_sort_by_last_ascending_puts_oldest_first(client, user, catalog):
    client.force_login(user)

    assert table_names(client, sort="last")[:4] == ["Присед", "Становая тяга", "Ажим", "Бжим"]


@pytest.mark.parametrize("junk", ["record", "--name", "-", "", " ", "DROP TABLE", "-record"])
def test_unknown_sort_falls_back_to_default(client, user, catalog, junk):
    client.force_login(user)

    assert table_names(client, sort=junk) == table_names(client)


def test_sort_combines_with_filters(client, user, catalog):
    client.force_login(user)

    assert table_names(client, sort="-name", group="Грудь") == ["Бжим", "Ажим"]
    assert table_names(client, sort="workouts", q="жим") == ["Ажим", "Бжим"]


def test_sort_combines_with_mine_filter(client, user, catalog):
    ExerciseFactory(name="Моё первое", owner=user)
    ExerciseFactory(name="Моё второе", owner=user)
    client.force_login(user)

    assert table_names(client, sort="-name", mine="1") == ["Моё первое", "Моё второе"]


def test_phone_layouts_ignore_sort(client, user, catalog):
    """Группы, плитки и общий список — в прежнем порядке при любом sort."""
    client.force_login(user)
    plain = client.get(reverse("exercise_list")).context
    sorted_ = client.get(reverse("exercise_list"), {"sort": "-workouts"}).context

    def names(items):
        return [item.name for item in items]

    assert names(sorted_["exercises"]) == names(plain["exercises"])
    assert names(sorted_["trained"]) == names(plain["trained"])
    assert [names(group["items"]) for group in sorted_["groups"]] == [
        names(group["items"]) for group in plain["groups"]
    ]


def test_other_users_workouts_do_not_move_rows(client, user, other_user):
    """Изоляция: чужие тренировки на общем упражнении не дают ни счётчика, ни
    даты и не поднимают строку наверх."""
    shared = ExerciseFactory(name="Жим лёжа")
    ExerciseFactory(name="Армейский жим")
    train(other_user, shared, days=1, times=5)

    client.force_login(user)
    rows = client.get(reverse("exercise_list"), {"sort": "-workouts"}).context["table_rows"]

    assert [row.name for row in rows] == ["Армейский жим", "Жим лёжа"]
    assert all(row.workouts_count == 0 and not row.last_workout_label for row in rows)


def test_other_users_personal_exercise_is_not_in_table(client, user, other_user):
    ExerciseFactory(name="Чужое упражнение", owner=other_user)
    ExerciseFactory(name="Жим лёжа")

    client.force_login(user)
    response = client.get(reverse("exercise_list"), {"sort": "name"})

    assert [row.name for row in response.context["table_rows"]] == ["Жим лёжа"]
    assert "Чужое упражнение" not in response.content.decode()


def test_table_meta_joins_equipment_unit_and_own_mark(client, user):
    ExerciseFactory(name="Жим лёжа", equipment="Штанга")
    ExerciseFactory(name="Планка своя", equipment="", measurement="time", owner=user)
    ExerciseFactory(name="Скручивания")

    client.force_login(user)
    rows = client.get(reverse("exercise_list"), {"sort": "name"}).context["table_rows"]
    meta = {row.name: row.table_meta for row in rows}

    assert meta == {"Жим лёжа": "Штанга", "Планка своя": "время · моё", "Скручивания": ""}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("name", ("name", False)),
        ("-name", ("name", True)),
        ("-workouts", ("workouts", True)),
        ("group", ("group", False)),
        (None, ("last", True)),
        ("--name", ("last", True)),
    ],
)
def test_parse_catalog_sort(raw, expected):
    assert parse_catalog_sort(raw) == expected


def test_last_workout_label_adds_year_only_for_other_years():
    today = date(2026, 10, 3)
    this_year = timezone.make_aware(datetime(2026, 8, 25, 12, 0))
    last_year = timezone.make_aware(datetime(2025, 12, 31, 12, 0))

    assert last_workout_label(this_year, today) == "25 авг"
    assert last_workout_label(last_year, today) == "31 дек 2025"
    assert last_workout_label(None, today) == ""


# ---------- Разметка таблицы ----------


def page(client, **params):
    return client.get(reverse("exercise_list"), params).content.decode()


def header_links(html):
    """href ссылок сортировки в шапке таблицы, по колонкам."""
    head = html.split("<thead>", 1)[1].split("</thead>", 1)[0]
    return dict(re.findall(r'class="app-catalog-col-(\w+)".*?href="([^"]+)"', head, re.S))


def test_table_has_exactly_one_sorted_column(client, user, catalog):
    client.force_login(user)

    html = page(client, sort="-workouts")

    assert html.count("aria-sort=") == 1
    assert 'class="app-catalog-col-workouts" aria-sort="descending"' in html


def test_active_column_link_reverses_direction(client, user, catalog):
    client.force_login(user)

    links = header_links(page(client, sort="-workouts"))

    assert links["workouts"].endswith("?sort=workouts")
    # Другие колонки включаются со своим направлением первого клика.
    assert links["name"].endswith("?sort=name")
    assert links["group"].endswith("?sort=group")


def test_link_to_default_sort_has_no_parameter(client, user, catalog):
    """По умолчанию — «Последняя» по убыванию: ссылка на неё — чистый адрес."""
    client.force_login(user)

    links = header_links(page(client, sort="name"))

    # {% querystring %} без параметров оставляет голый «?» — адрес тот же.
    assert links["last"].rstrip("?") == reverse("exercise_list")


def test_header_links_start_at_catalog_and_keep_filters(client, user, catalog):
    """Ссылки — от адреса каталога: после открытия шторки адрес страницы уже
    /exercises/5/, и относительный «?sort=…» увёл бы на деталь."""
    ExerciseFactory(name="Мой жим", muscle_group="Грудь", owner=user)
    client.force_login(user)

    links = header_links(page(client, q="жим", group="Грудь", mine="1"))

    for href in links.values():
        assert href.startswith(reverse("exercise_list") + "?")
        assert "q=%D0%B6%D0%B8%D0%BC" in href
        assert "mine=1" in href
        assert "group=" in href


def test_chips_and_search_keep_sort(client, user, catalog):
    client.force_login(user)

    html = page(client, sort="-workouts")

    chips = html.split('class="app-filter-axes"', 1)[1].split("</div>", 1)[0]
    assert "sort=-workouts" in chips
    assert '<input type="hidden" name="sort" value="-workouts">' in html


def test_search_has_no_sort_field_for_default_order(client, user, catalog):
    client.force_login(user)

    assert 'name="sort"' not in page(client)


def test_empty_result_has_no_table_but_keeps_hint(client, user, catalog):
    client.force_login(user)

    html = page(client, q="такого упражнения нет")

    assert "app-catalog-table" not in html
    assert "По этому запросу упражнений нет." in html


def test_table_offers_delete_only_for_own_exercises(client, user):
    ExerciseFactory(name="Жим лёжа")
    own = ExerciseFactory(name="Моё упражнение", owner=user)

    client.force_login(user)
    table = page(client).split("<tbody>", 1)[1].split("</tbody>", 1)[0]

    assert table.count("app-row-delete") == 1
    assert reverse("exercise_delete", args=[own.pk]) in table


def test_table_rows_carry_js_hooks(client, user, catalog):
    """По data-атрибутам exercise.js делает строку кликабельной и подсвечивает
    открытое упражнение; ссылка-название — то, что откроет шторка."""
    client.force_login(user)

    table = page(client).split("<tbody>", 1)[1].split("</tbody>", 1)[0]

    assert table.count("<tr data-exercise-item data-exercise-row>") == 6
    assert table.count("data-exercise-link") == 6
