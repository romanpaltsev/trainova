"""«Статистика» дашборда на ПК: время, тоннаж и дистанция по месяцам за год."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts import stats
from workouts.models import Sport
from workouts.tests.factories import (
    CardioPartFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)
from workouts.tests.test_query_budget import fill_history

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 3)


def local_dt(year, month, day, hour=12, minute=0):
    return timezone.make_aware(datetime(year, month, day, hour, minute))


def tab(data, key):
    return next(item for item in data["tabs"] if item["key"] == key)


def values(data, key, name):
    dataset = next(item for item in tab(data, key)["datasets"] if item["name"] == name)
    return dataset["values"]


@pytest.fixture
def strength():
    return SportFactory(name="Силовая", category=Sport.Category.STRENGTH, owner=None)


@pytest.fixture
def run():
    return SportFactory(name="Бег", category=Sport.Category.CARDIO, owner=None)


def test_months_run_from_eleven_months_ago_to_current():
    starts = stats.month_starts(TODAY, 12)

    assert starts[0] == date(2025, 11, 1)
    assert starts[-1] == date(2026, 10, 1)
    assert len(starts) == 12


def test_monthly_stats_labels_are_russian_months(user):
    data = stats.monthly_stats(user, today=TODAY)

    assert data["labels"][-1] == "окт"
    assert data["titles"][-1] == "Октябрь 2026"


def test_monthly_time_splits_mixed_workout_between_host_and_part(user, strength, run):
    """Как в «часах по неделям»: части получают свои минуты, хозяину — остаток."""
    mixed = WorkoutFactory(
        user=user, sport=strength, started_at=local_dt(2026, 9, 10), duration_min=90
    )
    StrengthSetFactory(workout=mixed, set_number=1)
    CardioPartFactory(workout=mixed, sport=run, duration_min=30, distance_km=Decimal("5"))

    data = stats.monthly_stats(user, today=TODAY)

    assert values(data, "time", "Силовая")[-2] == 1.0
    assert values(data, "time", "Бег")[-2] == 0.5
    assert values(data, "distance", "Бег")[-2] == 5.0
    assert tab(data, "time")["total"] == "2 ч"


def test_monthly_tonnage_in_tonnes(user, strength):
    workout = WorkoutFactory(user=user, sport=strength, started_at=local_dt(2026, 10, 1))
    StrengthSetFactory(workout=workout, set_number=1, weight_kg=100, reps=10)
    StrengthSetFactory(workout=workout, set_number=2, weight_kg=Decimal("82.5"), reps=10)

    data = stats.monthly_stats(user, today=TODAY)

    assert values(data, "tonnage", "Тоннаж")[-1] == 1.82
    assert tab(data, "tonnage")["total"] == "1,8 т"


def test_month_is_taken_by_local_date(user, strength):
    """1 октября 00:30 по Москве — это ещё 30 сентября по UTC, а месяц — октябрь."""
    WorkoutFactory(
        user=user,
        sport=strength,
        started_at=local_dt(2026, 10, 1, hour=0, minute=30),
        duration_min=60,
    )

    data = stats.monthly_stats(user, today=TODAY)

    assert values(data, "time", "Силовая")[-1] == 1.0
    assert values(data, "time", "Силовая")[-2] == 0.0


def test_monthly_stats_skip_old_unfinished_and_foreign(user, other_user, strength):
    WorkoutFactory(user=user, sport=strength, started_at=local_dt(2025, 10, 31), duration_min=60)
    WorkoutFactory(user=user, sport=strength, started_at=timezone.now(), duration_min=None)
    WorkoutFactory(
        user=other_user, sport=strength, started_at=local_dt(2026, 9, 1), duration_min=60
    )

    data = stats.monthly_stats(user, today=TODAY)

    assert all(not item["datasets"] for item in data["tabs"])


def test_stats_card_requires_login(client):
    response = client.get(reverse("dashboard_stats"))

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_stats_card_renders_tabs_and_data(client, user):
    fill_history(user, weeks=2)
    client.force_login(user)

    content = client.get(reverse("dashboard_stats")).content.decode()

    assert "data-stats" in content
    assert 'data-stats-tab="time"' in content
    assert 'data-stats-tab="distance"' in content
    assert 'id="stats-data"' in content


def test_stats_card_without_data_shows_hint(client, user, other_user):
    """Чужие тренировки статистику не наполняют — у пользователя её нет."""
    fill_history(other_user, weeks=2)
    client.force_login(user)

    content = client.get(reverse("dashboard_stats")).content.decode()

    assert "Статистика за год появится" in content
    assert "data-stats-tab" not in content


def test_dashboard_has_lazy_stats_placeholder(client, user):
    """Дашборд рендерит только заготовку: данные карточка забирает сама, когда
    видна, — на телефоне она скрыта, и запроса нет вовсе."""
    client.force_login(user)

    content = client.get(reverse("dashboard")).content.decode()

    assert f'hx-get="{reverse("dashboard_stats")}"' in content
    assert 'hx-trigger="intersect once"' in content
    assert 'id="stats-data"' not in content


@pytest.mark.parametrize("weeks", [2, 8])
def test_stats_card_query_budget(client, user, django_assert_max_num_queries, weeks):
    """Сессия, пользователь и пара SAVEPOINT/RELEASE транзакции запроса, а
    данные — тренировки, тоннаж, кардио-части и виды спорта: четыре запроса на
    все три вкладки, сколько бы ни было истории."""
    fill_history(user, weeks=weeks)
    client.force_login(user)

    with django_assert_max_num_queries(8):
        client.get(reverse("dashboard_stats"))
