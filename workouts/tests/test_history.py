"""Лента истории: изоляция, фильтр, группировка по неделям, подгрузка страниц."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.models import Sport
from workouts.tests.factories import CardioDetailsFactory, SportFactory, WorkoutFactory

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
    assert str(alien.pk) not in response.content.decode()


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
    assert reverse("cardio_create") in content
