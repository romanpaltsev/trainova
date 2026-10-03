"""Цель на неделю: ввод часов, модалка из карточки дашборда, границы в базе."""

from datetime import date, timedelta

import pytest
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from accounts.forms import WeeklyGoalForm
from workouts import stats
from workouts.tests.factories import WorkoutFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("raw", "minutes"),
    [
        ("4", 240),
        ("4,5", 270),
        ("4.5", 270),
        ("4:30", 270),
        (" 3:05 ", 185),
        ("0:30", 30),
        ("40", 2400),
    ],
)
def test_goal_form_accepts_hours_in_any_usual_spelling(raw, minutes):
    form = WeeklyGoalForm({"hours": raw})

    assert form.is_valid(), form.errors
    assert form.cleaned_data["hours"] == minutes


@pytest.mark.parametrize(
    "raw",
    ["", "abc", "4:75", "NaN", "Infinity", "-3", "0", "0:20", "41", "40:01"],
)
def test_goal_form_rejects_garbage_and_out_of_range(raw):
    """NaN и Infinity Decimal принимает, а round() на них падает — это 500,
    а не подсказка, если не отсечь их заранее."""
    assert not WeeklyGoalForm({"hours": raw}).is_valid()


def test_goal_modal_requires_login(client):
    response = client.get(reverse("profile_goal"))

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_goal_modal_shows_current_goal(client, user):
    user.weekly_goal_minutes = 270
    user.save()
    client.force_login(user)

    content = client.get(reverse("profile_goal")).content.decode()

    assert 'value="4:30"' in content
    assert "Убрать цель" in content


def test_goal_modal_without_goal_has_no_clear_button(client, user):
    client.force_login(user)

    content = client.get(reverse("profile_goal")).content.decode()

    assert "Убрать цель" not in content


def test_saving_goal_returns_card_out_of_band(client, user):
    """Ответ — карточка цели OOB и больше ничего: модалка закрывается сама."""
    WorkoutFactory(user=user, started_at=timezone.now(), duration_min=60)
    client.force_login(user)

    content = client.post(reverse("profile_goal"), {"hours": "4"}).content.decode()

    user.refresh_from_db()
    assert user.weekly_goal_minutes == 240
    assert 'id="week-goal"' in content
    assert 'hx-swap-oob="true"' in content
    assert "25%" in content
    assert "app-modal" not in content


def test_invalid_goal_keeps_modal_with_hint(client, user):
    client.force_login(user)

    content = client.post(reverse("profile_goal"), {"hours": "сорок"}).content.decode()

    user.refresh_from_db()
    assert user.weekly_goal_minutes is None
    assert "app-modal" in content
    assert "Введите часы" in content


def test_clear_removes_goal(client, user):
    user.weekly_goal_minutes = 240
    user.save()
    client.force_login(user)

    content = client.post(reverse("profile_goal"), {"hours": "4", "clear": "1"}).content.decode()

    user.refresh_from_db()
    assert user.weekly_goal_minutes is None
    assert "Задать цель" in content


def test_goal_change_does_not_touch_other_users(client, user, other_user):
    client.force_login(user)

    client.post(reverse("profile_goal"), {"hours": "5"})

    other_user.refresh_from_db()
    assert other_user.weekly_goal_minutes is None


def test_goal_card_counts_only_own_workouts(client, user, other_user):
    """Прогресс недели — только свои тренировки: чужие часы цель не двигают."""
    user.weekly_goal_minutes = 120
    user.save()
    WorkoutFactory(user=user, started_at=timezone.now(), duration_min=30)
    WorkoutFactory(user=other_user, started_at=timezone.now(), duration_min=90)
    client.force_login(user)

    content = client.get(reverse("dashboard")).content.decode()

    assert "25%" in content
    assert "0:30 из 2:00" in content


def test_dashboard_invites_to_set_goal(client, user):
    client.force_login(user)

    content = client.get(reverse("dashboard")).content.decode()

    assert 'id="week-goal"' in content
    assert "Задать цель" in content


def test_database_rejects_goal_out_of_range(user):
    """Границы держит и база: цель правят и через админку, а ноль дал бы
    деление на ноль в карточке."""
    user.weekly_goal_minutes = 10
    with pytest.raises(IntegrityError):
        user.save()


# ---------- Расчёт карточки ----------

TOTALS = [0] * 10 + [200, 90]  # …, прошлая неделя, эта неделя


def test_week_goal_without_target_has_no_progress():
    goal = stats.week_goal(None, TOTALS, timezone.localdate())

    assert goal["target"] is None
    assert "percent" not in goal
    assert goal["done_display"] == "1:30"


def test_week_goal_progress_and_remaining():
    goal = stats.week_goal(240, TOTALS, timezone.localdate())

    assert goal["percent"] == 38  # 90 / 240
    assert goal["arc"] == 38
    assert goal["message"] == "До цели осталось 2:30."
    assert (goal["target_display"], goal["last_display"]) == ("4:00", "3:20")


def test_week_goal_over_target_keeps_honest_percent_and_full_arc():
    goal = stats.week_goal(60, TOTALS, timezone.localdate())

    assert goal["percent"] == 150
    assert goal["arc"] == 100
    assert goal["message"] == "Цель недели выполнена! Сверх цели — 0:30."


def test_week_goal_exactly_reached():
    goal = stats.week_goal(90, TOTALS, timezone.localdate())

    assert goal["message"] == "Цель недели выполнена!"


def test_week_goal_spans_calendar_week():
    wednesday = date(2026, 10, 7)

    goal = stats.week_goal(60, TOTALS, wednesday)

    assert goal["start"] == wednesday - timedelta(days=2)
    assert goal["end"] == wednesday + timedelta(days=4)


def test_weekly_chart_totals_sum_whole_workouts(user, other_user):
    """Минуты недели — длительности тренировок целиком, у смешанной тоже:
    её части уже внутри общей длительности."""
    now = timezone.now()
    WorkoutFactory(user=user, started_at=now, duration_min=50)
    WorkoutFactory(user=user, started_at=now - timedelta(days=7), duration_min=40)
    WorkoutFactory(user=other_user, started_at=now, duration_min=500)

    totals = stats.weekly_chart(user)["totals"]

    assert totals[-1] == 50
    assert totals[-2] == 40
    assert len(totals) == 12
