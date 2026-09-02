"""Лента истории: изоляция, фильтр, группировка по неделям, подгрузка страниц."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from workouts.models import Sport
from workouts.tests.factories import (
    CardioDetailsFactory,
    ExerciseFactory,
    LocationFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def days_ago(days):
    return timezone.now() - timedelta(days=days)


def test_feed_shows_only_own_workouts(client, user, other_user):
    client.force_login(user)
    mine = WorkoutFactory(user=user)
    alien = WorkoutFactory(user=other_user)

    response = client.get(reverse("workout_history"))
    workouts = list(response.context["workouts"])

    assert workouts == [mine]
    assert alien not in workouts
    # Не голый pk: маленькое число нашлось бы в датах и весах и дало бы флак.
    assert f'id="workout-{alien.pk}"' not in response.content.decode()


def test_feed_is_ordered_by_date_descending(client, user):
    client.force_login(user)
    older = WorkoutFactory(user=user, started_at=days_ago(10))
    newer = WorkoutFactory(user=user, started_at=days_ago(1))

    response = client.get(reverse("workout_history"))

    assert list(response.context["workouts"]) == [newer, older]


def test_cardio_card_shows_distance_time_and_pace(client, user):
    client.force_login(user)
    run = SportFactory(name="Бег", category=Sport.Category.CARDIO)
    workout = WorkoutFactory(user=user, sport=run, duration_min=41)
    CardioDetailsFactory(workout=workout, distance_km="7.2")

    content = client.get(reverse("workout_history")).content.decode()

    assert "0:41" in content
    assert "7,2 км" in content
    assert "5:41 /км" in content
    assert "темп" in content


def test_filter_by_sport(client, user):
    client.force_login(user)
    bike = SportFactory(name="Велосипед", category=Sport.Category.CARDIO)
    run = SportFactory(name="Бег", category=Sport.Category.CARDIO)
    on_bike = WorkoutFactory(user=user, sport=bike)
    WorkoutFactory(user=user, sport=run)

    response = client.get(reverse("workout_history"), {"sport": bike.pk})

    assert list(response.context["workouts"]) == [on_bike]


def test_filter_chips_show_only_used_sports(client, user):
    client.force_login(user)
    used = SportFactory(name="Велосипед", category=Sport.Category.CARDIO)
    unused = SportFactory(name="Лыжи", category=Sport.Category.CARDIO)
    WorkoutFactory(user=user, sport=used)

    sports = client.get(reverse("workout_history")).context["sports_used"]

    assert list(sports) == [used]
    assert unused not in sports


def test_weeks_are_grouped_with_titles(client, user):
    client.force_login(user)
    WorkoutFactory(user=user, started_at=timezone.now())
    WorkoutFactory(user=user, started_at=days_ago(7))
    WorkoutFactory(user=user, started_at=days_ago(30))

    groups = client.get(reverse("workout_history")).context["groups"]
    titles = [group["title"] for group in groups]

    assert titles[0] == "Эта неделя"
    assert "Прошлая неделя" in titles
    assert len(groups) >= 3
    assert titles[-1] not in ("Эта неделя", "Прошлая неделя")


def test_second_page_is_loaded_by_htmx_without_repeating_week_title(client, user):
    client.force_login(user)
    # 12 тренировок одной недели: не влезают на страницу и попадают в одну группу.
    for hour in range(12):
        WorkoutFactory(user=user, started_at=timezone.now() - timedelta(hours=hour))

    first = client.get(reverse("workout_history"))
    assert len(first.context["workouts"]) == 10
    assert first.context["page_obj"].has_next
    assert "Показать ещё" in first.content.decode()

    last_week = first.context["last_week"]
    second = client.get(
        reverse("workout_history"),
        {"page": 2, "prev_week": last_week},
        headers={"HX-Request": "true"},
    )

    assert len(second.context["workouts"]) == 2
    assert "Эта неделя" not in second.content.decode()
    assert "Показать ещё" not in second.content.decode()


def test_empty_feed_offers_to_record_workout(client, user):
    client.force_login(user)

    content = client.get(reverse("workout_history")).content.decode()

    assert "Тренировок пока нет" in content
    # Кнопка открывает чузер «+»: оттуда доступны и силовая, и кардио.
    assert reverse("workout_start") in content


def test_order_is_stable_when_started_at_is_identical(client, user):
    """У двух тренировок одного прошедшего дня started_at совпадает — нужен тайбрейкер."""
    client.force_login(user)
    same_moment = timezone.now() - timedelta(days=1)
    first = WorkoutFactory(user=user, started_at=same_moment)
    second = WorkoutFactory(user=user, started_at=same_moment)

    orders = {
        tuple(w.pk for w in client.get(reverse("workout_history")).context["workouts"])
        for _ in range(5)
    }

    assert orders == {(second.pk, first.pk)}


def test_feed_does_not_scale_queries_with_cards(client, user, django_assert_num_queries):
    """Число запросов не должно расти вместе с числом карточек."""
    client.force_login(user)
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    bike = SportFactory(name="Велосипед", category=Sport.Category.CARDIO)
    for _ in range(3):
        StrengthSetFactory(workout=WorkoutFactory(user=user, sport=strength), set_number=1)
        CardioDetailsFactory(workout__user=user, workout__sport=bike)

    with CaptureQueriesContext(connection) as few:
        client.get(reverse("workout_history"))
    for _ in range(2):
        StrengthSetFactory(workout=WorkoutFactory(user=user, sport=strength), set_number=1)
    with CaptureQueriesContext(connection) as more:
        client.get(reverse("workout_history"))

    assert len(more.captured_queries) == len(few.captured_queries)


def test_strength_card_shows_exercises_and_tonnage(client, user):
    client.force_login(user)
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    workout = WorkoutFactory(user=user, sport=strength, duration_min=62)
    bench = ExerciseFactory(name="Жим лёжа")
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1, weight_kg=80, reps=8)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=2, weight_kg=80, reps=8)
    StrengthSetFactory(
        workout=workout,
        exercise=ExerciseFactory(name="Присед"),
        set_number=1,
        weight_kg=100,
        reps=5,
    )

    content = client.get(reverse("workout_history")).content.decode()

    assert "упражнений" in content
    assert "тоннаж" in content
    assert "1780 кг" in content  # 80*8 + 80*8 + 100*5


# ---------- Фильтр по месту ----------


def test_filter_by_location(client, user):
    client.force_login(user)
    gym = LocationFactory(owner=user, name="СпортЛайф")
    park = LocationFactory(owner=user, name="Парк у реки")
    in_gym = WorkoutFactory(user=user, location=gym)
    WorkoutFactory(user=user, location=park)
    WorkoutFactory(user=user)  # без места

    response = client.get(reverse("workout_history"), {"location": gym.pk})

    assert list(response.context["workouts"]) == [in_gym]


def test_filters_combine_sport_and_location(client, user):
    client.force_login(user)
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    bike = SportFactory(name="Велосипед", category=Sport.Category.CARDIO)
    gym = LocationFactory(owner=user, name="СпортЛайф")
    wanted = WorkoutFactory(user=user, sport=strength, location=gym)
    WorkoutFactory(user=user, sport=bike, location=gym)
    WorkoutFactory(user=user, sport=strength)

    response = client.get(reverse("workout_history"), {"sport": strength.pk, "location": gym.pk})

    assert list(response.context["workouts"]) == [wanted]


def test_location_chips_show_only_used_locations(client, user):
    """Чипы строятся по записанным тренировкам: неиспользованное место лишнее."""
    client.force_login(user)
    used = LocationFactory(owner=user, name="СпортЛайф")
    LocationFactory(owner=user, name="Никогда не был")
    draft_only = LocationFactory(owner=user, name="Только в черновике")
    WorkoutFactory(user=user, location=used)
    WorkoutFactory(user=user, location=draft_only, started_at=None, duration_min=None)

    response = client.get(reverse("workout_history"))

    assert list(response.context["locations_used"]) == [used]


def test_location_chips_ignore_other_users_workouts(client, user, other_user):
    """Изоляция: чужая тренировка не приводит место в мои чипы."""
    client.force_login(user)
    place = LocationFactory(owner=user, name="СпортЛайф")
    WorkoutFactory(user=other_user, location=place)

    response = client.get(reverse("workout_history"))

    assert list(response.context["locations_used"]) == []


def test_foreign_location_filter_gives_empty_feed(client, user, other_user):
    client.force_login(user)
    theirs = LocationFactory(owner=other_user, name="Чужой зал")
    WorkoutFactory(user=other_user, location=theirs)
    WorkoutFactory(user=user)

    response = client.get(reverse("workout_history"), {"location": theirs.pk})

    assert list(response.context["workouts"]) == []
    assert "По этому месту тренировок пока нет." in response.content.decode()


@pytest.mark.parametrize("raw", ["abc", "", "-1"])
def test_unknown_location_filter_is_ignored(client, user, raw):
    client.force_login(user)
    workout = WorkoutFactory(user=user)

    response = client.get(reverse("workout_history"), {"location": raw})

    assert list(response.context["workouts"]) == [workout]


def test_chips_keep_the_other_filter_and_reset_pagination(client, user):
    """Одна ось не стирает другую, а страница сбрасывается на первую."""
    client.force_login(user)
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    gym = LocationFactory(owner=user, name="СпортЛайф")
    WorkoutFactory(user=user, sport=strength, location=gym)

    content = client.get(
        reverse("workout_history"), {"sport": strength.pk, "page": 1}
    ).content.decode()
    # Смотрим только на блок чипов: «page=» законно есть у кнопки «Показать ещё».
    start = content.index("app-filter-axes")
    chips = content[start : content.index('id="feed"', start)]

    assert f"location={gym.pk}" in chips
    assert f"sport={strength.pk}" in chips
    assert "page=" not in chips


def test_load_more_keeps_both_filters(client, user):
    """Ручная склейка URL теряла второй фильтр — тег querystring сохраняет оба."""
    client.force_login(user)
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    gym = LocationFactory(owner=user, name="СпортЛайф")
    for hour in range(12):
        WorkoutFactory(
            user=user,
            sport=strength,
            location=gym,
            started_at=timezone.now() - timedelta(hours=hour),
        )

    content = client.get(
        reverse("workout_history"), {"sport": strength.pk, "location": gym.pk}
    ).content.decode()
    button = content[content.index('id="load-more"') :]

    assert f"sport={strength.pk}" in button
    assert f"location={gym.pk}" in button
    assert "page=2" in button


def test_card_shows_the_location(client, user):
    client.force_login(user)
    place = LocationFactory(owner=user, name="СпортЛайф")
    WorkoutFactory(user=user, location=place)

    content = client.get(reverse("workout_history")).content.decode()

    assert "СпортЛайф" in content
    assert "app-workout-meta" in content


def test_card_without_location_has_no_meta_wrapper(client, user):
    """Карточка без места и заметки обязана рендериться как до появления фичи."""
    client.force_login(user)
    WorkoutFactory(user=user, note="")

    content = client.get(reverse("workout_history")).content.decode()

    assert "app-workout-meta" not in content


def test_location_axis_is_absent_without_places(client, user):
    """У старого пользователя история не меняется до первой отметки места."""
    client.force_login(user)
    WorkoutFactory(user=user)

    content = client.get(reverse("workout_history")).content.decode()

    assert "app-filter-axes" in content  # ось видов спорта осталась
    assert "Везде" not in content
