"""Агрегации дашборда: сводка за 7 дней, недельные суммы, рекорды, прогресс."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from workouts import stats
from workouts.models import Sport
from workouts.tests.factories import (
    CardioDetailsFactory,
    ExerciseFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db

# Среда — окно «за 7 дней» захватывает хвост прошлой календарной недели.
TODAY = date(2026, 8, 26)


def local_dt(year, month, day, hour=12, minute=0):
    return timezone.make_aware(datetime(year, month, day, hour, minute))


def workout_on(user, day, minutes=60, **kwargs):
    return WorkoutFactory(
        user=user,
        started_at=local_dt(day.year, day.month, day.day),
        duration_min=minutes,
        **kwargs,
    )


# ---------- Сводка за 7 дней ----------


def test_summary_counts_only_current_window(user):
    workout_on(user, TODAY)  # день 0
    workout_on(user, TODAY - timedelta(days=6))  # граница окна — внутри
    workout_on(user, TODAY - timedelta(days=7))  # уже прошлое окно
    workout_on(user, TODAY - timedelta(days=14))  # вне обоих окон

    summary = stats.seven_day_summary(user, today=TODAY)

    assert summary["count"] == 2
    assert summary["count_delta"] == 2 - 1


def test_summary_includes_late_evening_workout_of_today(user):
    WorkoutFactory(
        user=user,
        started_at=local_dt(TODAY.year, TODAY.month, TODAY.day, hour=23, minute=50),
        duration_min=45,
    )

    summary = stats.seven_day_summary(user, today=TODAY)

    assert summary["count"] == 1
    assert summary["minutes"] == 45


def test_summary_deltas_against_previous_window(user):
    workout_on(user, TODAY, minutes=62)
    workout_on(user, TODAY - timedelta(days=2), minutes=41)
    workout_on(user, TODAY - timedelta(days=8), minutes=65)

    summary = stats.seven_day_summary(user, today=TODAY)

    assert summary["count_delta"] == 1
    assert summary["minutes_delta"] == 62 + 41 - 65
    assert summary["duration_display"] == "1:43"


def test_summary_deltas_when_previous_window_is_empty(user):
    workout_on(user, TODAY, minutes=30)

    summary = stats.seven_day_summary(user, today=TODAY)

    assert summary["count_delta"] == 1
    assert summary["minutes_delta"] == 30


def test_summary_tonnage_and_strength_count(user):
    first = workout_on(user, TODAY)
    StrengthSetFactory(workout=first, set_number=1, weight_kg=80, reps=10)
    second = workout_on(user, TODAY - timedelta(days=1))
    StrengthSetFactory(workout=second, set_number=1, weight_kg=Decimal("77.5"), reps=8)

    summary = stats.seven_day_summary(user, today=TODAY)

    assert summary["strength_count"] == 2
    assert summary["tonnage_display"] == "1420"  # 800 + 620


def test_summary_distance_and_cardio_sport_names(user):
    bike = CardioDetailsFactory(
        workout__user=user,
        workout__started_at=local_dt(TODAY.year, TODAY.month, TODAY.day),
        workout__sport__name="Велосипед",
        distance_km=Decimal("32.4"),
    )
    CardioDetailsFactory(
        workout__user=user,
        workout__started_at=local_dt(TODAY.year, TODAY.month, TODAY.day - 1),
        workout__sport__name="Бег",
        distance_km=Decimal("7.2"),
    )
    assert bike.workout.sport.name == "Велосипед"

    summary = stats.seven_day_summary(user, today=TODAY)

    assert summary["distance_display"] == "39,6"
    assert summary["cardio_sports"] == ["Бег", "Велосипед"]


def test_summary_ignores_active_workout(user):
    WorkoutFactory(user=user, duration_min=None, started_at=local_dt(2026, 8, 26))

    summary = stats.seven_day_summary(user, today=TODAY)

    assert summary["count"] == 0


def test_summary_ignores_other_users_workouts(user, other_user):
    workout_on(other_user, TODAY)

    summary = stats.seven_day_summary(user, today=TODAY)

    assert summary["count"] == 0


# ---------- График «часы по неделям» ----------


@pytest.mark.parametrize("today", [TODAY, date(2026, 1, 15)], ids=["mid-year", "year-crossing"])
def test_weekly_chart_returns_twelve_weeks_with_zero_fill(user, today):
    workout_on(user, today, minutes=90)

    chart = stats.weekly_chart(user, today=today)

    assert len(chart["labels"]) == 12
    assert len(chart["starts"]) == 12
    hours = chart["datasets"][0]["hours"]
    assert len(hours) == 12
    assert hours[-1] == 1.5
    assert all(value == 0 for value in hours[:-1])


def test_weekly_chart_splits_hours_by_sport(user):
    monday = stats.week_start(TODAY)
    workout_on(user, monday, minutes=60)
    CardioDetailsFactory(
        workout__user=user,
        workout__started_at=local_dt(monday.year, monday.month, monday.day + 1),
        workout__duration_min=30,
        workout__sport__name="Велосипед",
    )

    chart = stats.weekly_chart(user, today=TODAY)

    by_name = {dataset["name"]: dataset["hours"][-1] for dataset in chart["datasets"]}
    assert by_name[chart["datasets"][0]["name"]] == 1.0
    assert by_name["Велосипед"] == 0.5


def test_weekly_chart_orders_sports_strength_first_then_by_name(user):
    workout_on(user, TODAY, sport__name="Кроссфит", sport__owner=user)
    workout_on(user, TODAY - timedelta(days=1), sport__name="Силовая")
    CardioDetailsFactory(
        workout__user=user,
        workout__started_at=local_dt(TODAY.year, TODAY.month, TODAY.day),
        workout__sport__name="Бег",
        workout__sport__category=Sport.Category.CARDIO,
    )

    chart = stats.weekly_chart(user, today=TODAY)

    assert [dataset["name"] for dataset in chart["datasets"]] == ["Кроссфит", "Силовая", "Бег"]


def test_weekly_chart_is_empty_for_new_user(user):
    chart = stats.weekly_chart(user, today=TODAY)

    assert len(chart["labels"]) == 12
    assert chart["datasets"] == []


def test_weekly_chart_assigns_monday_night_workout_to_new_week(user):
    """Пн 00:10 МСК — это ещё вс 21:10 по UTC: неделя должна браться по локали."""
    monday = stats.week_start(TODAY)
    WorkoutFactory(
        user=user,
        started_at=local_dt(monday.year, monday.month, monday.day, hour=0, minute=10),
        duration_min=60,
    )

    chart = stats.weekly_chart(user, today=TODAY)

    hours = chart["datasets"][0]["hours"]
    assert hours[-1] == 1.0  # текущая неделя
    assert hours[-2] == 0  # не прошлая


def test_weekly_chart_ignores_other_users_workouts(user, other_user):
    workout_on(other_user, TODAY)

    chart = stats.weekly_chart(user, today=TODAY)

    assert chart["datasets"] == []


# ---------- Последние тренировки ----------


def test_latest_workouts_limits_to_five_newest(user):
    for offset in range(7):
        workout_on(user, TODAY - timedelta(days=offset))

    rows = stats.latest_workouts(user, today=TODAY)

    assert len(rows) == 5
    dates = [timezone.localtime(row["workout"].started_at).date() for row in rows]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.parametrize(
    ("days_ago", "expected"),
    [
        pytest.param(0, "сегодня", id="today"),
        pytest.param(1, "вчера", id="yesterday"),
        pytest.param(4, "сб", id="weekday"),  # 22.08.2026 — суббота
        pytest.param(10, "16 авг", id="date"),
    ],
)
def test_latest_workouts_day_labels(user, days_ago, expected):
    workout_on(user, TODAY - timedelta(days=days_ago))

    rows = stats.latest_workouts(user, today=TODAY)

    assert rows[0]["meta"].startswith(expected + " · ")


def test_latest_workouts_metric_for_strength_and_cardio(user):
    strength = workout_on(user, TODAY, minutes=62)
    StrengthSetFactory(workout=strength, set_number=1, weight_kg=80, reps=10)
    CardioDetailsFactory(
        workout__user=user,
        workout__started_at=local_dt(TODAY.year, TODAY.month, TODAY.day - 1),
        workout__duration_min=84,
        distance_km=Decimal("32.4"),
    )

    rows = stats.latest_workouts(user, today=TODAY)

    assert rows[0]["meta"] == "сегодня · 1:02 · 800 кг"
    assert rows[1]["meta"] == "вчера · 1:24 · 32,4 км"


def test_latest_workouts_ignore_other_users_workouts(user, other_user):
    workout_on(other_user, TODAY)

    assert stats.latest_workouts(user, today=TODAY) == []


# ---------- Рекорды ----------


def test_strength_records_take_max_weight_per_exercise(user):
    bench = ExerciseFactory(name="Жим лёжа")
    squat = ExerciseFactory(name="Присед")
    first = workout_on(user, TODAY - timedelta(days=10))
    StrengthSetFactory(workout=first, exercise=bench, set_number=1, weight_kg=80, reps=5)
    second = workout_on(user, TODAY)
    StrengthSetFactory(
        workout=second, exercise=bench, set_number=1, weight_kg=Decimal("82.5"), reps=3
    )
    StrengthSetFactory(workout=second, exercise=squat, set_number=1, weight_kg=110, reps=5)

    records = stats.strength_records(user)

    assert [(record["name"], record["weight_display"]) for record in records] == [
        ("Присед", "110"),
        ("Жим лёжа", "82,5"),
    ]


def test_strength_records_exclude_zero_weight_sets(user):
    plank = ExerciseFactory(name="Планка")
    workout = workout_on(user, TODAY)
    StrengthSetFactory(workout=workout, exercise=plank, set_number=1, weight_kg=0, reps=60)

    assert stats.strength_records(user) == []


def test_strength_records_ignore_active_workout_sets(user):
    active = WorkoutFactory(user=user, duration_min=None)
    StrengthSetFactory(workout=active, set_number=1, weight_kg=200, reps=1, done=False)

    assert stats.strength_records(user) == []


def test_strength_records_ignore_other_users_sets(user, other_user):
    workout = workout_on(other_user, TODAY)
    StrengthSetFactory(workout=workout, set_number=1, weight_kg=100, reps=5)

    assert stats.strength_records(user) == []


def test_cardio_records_take_max_distance_per_sport(user):
    bike = SportFactory(name="Велосипед", category=Sport.Category.CARDIO)
    for day, distance in ((TODAY, "32.4"), (TODAY - timedelta(days=3), "64")):
        CardioDetailsFactory(
            workout__user=user,
            workout__started_at=local_dt(day.year, day.month, day.day),
            workout__sport=bike,
            workout__duration_min=120,
            distance_km=Decimal(distance),
        )

    records = stats.cardio_records(user)

    assert len(records) == 1
    assert records[0]["distance_display"] == "64"


def test_cardio_records_show_speed_above_threshold(user):
    CardioDetailsFactory(
        workout__user=user,
        workout__sport__name="Велосипед",
        workout__duration_min=84,
        distance_km=Decimal("32.4"),  # 23,1 км/ч
    )

    record = stats.cardio_records(user)[0]

    assert record["metric_label"] == "скорость"
    assert record["metric_display"] == "23,1 км/ч"


def test_cardio_records_show_pace_below_threshold(user):
    CardioDetailsFactory(
        workout__user=user,
        workout__sport__name="Бег",
        workout__duration_min=41,
        distance_km=Decimal("7.2"),  # 10,5 км/ч → 5:41 /км
    )

    record = stats.cardio_records(user)[0]

    assert record["metric_label"] == "темп"
    assert record["metric_display"] == "5:41 /км"


def test_cardio_records_choose_unit_by_best_workout(user):
    """Один вид спорта, медленная и быстрая тренировки — юнит по лучшей."""
    bike = SportFactory(name="Велосипед", category=Sport.Category.CARDIO)
    for minutes, distance in ((60, "10"), (60, "20")):
        CardioDetailsFactory(
            workout__user=user,
            workout__sport=bike,
            workout__duration_min=minutes,
            distance_km=Decimal(distance),
        )

    record = stats.cardio_records(user)[0]

    assert record["metric_display"] == "20,0 км/ч"


def test_cardio_records_skip_zero_distance(user):
    CardioDetailsFactory(workout__user=user, distance_km=0)

    assert stats.cardio_records(user) == []


def test_cardio_records_ignore_other_users_workouts(user, other_user):
    CardioDetailsFactory(workout__user=other_user, distance_km=10)

    assert stats.cardio_records(user) == []


# ---------- Прогресс упражнения и прожектор ----------


def test_exercise_progress_orders_workouts_by_date(user):
    bench = ExerciseFactory()
    late = workout_on(user, TODAY)
    StrengthSetFactory(workout=late, exercise=bench, set_number=1, weight_kg=80, reps=5)
    early = workout_on(user, TODAY - timedelta(days=7))
    StrengthSetFactory(workout=early, exercise=bench, set_number=1, weight_kg=70, reps=5)

    progress = stats.exercise_progress(user, bench)

    assert [group["workout"] for group in progress] == [early, late]
    assert [group["max_weight"] for group in progress] == [70.0, 80.0]


def test_exercise_progress_groups_sets_with_max_weight(user):
    bench = ExerciseFactory()
    workout = workout_on(user, TODAY)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1, weight_kg=70, reps=10)
    StrengthSetFactory(
        workout=workout, exercise=bench, set_number=2, weight_kg=Decimal("77.5"), reps=8
    )

    progress = stats.exercise_progress(user, bench)

    assert len(progress) == 1
    assert progress[0]["max_weight"] == 77.5
    assert [row.set_number for row in progress[0]["sets"]] == [1, 2]


def test_exercise_progress_ignores_active_workout(user):
    bench = ExerciseFactory()
    active = WorkoutFactory(user=user, duration_min=None)
    StrengthSetFactory(workout=active, exercise=bench, set_number=1, done=False)

    assert stats.exercise_progress(user, bench) == []


def test_exercise_progress_ignores_other_users_workouts(user, other_user):
    bench = ExerciseFactory()
    workout = workout_on(other_user, TODAY)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1)

    assert stats.exercise_progress(user, bench) == []


def test_exercise_spotlight_picks_top_record_with_sparkline(user):
    bench = ExerciseFactory(name="Жим лёжа")
    for offset in range(14):
        workout = workout_on(user, TODAY - timedelta(days=offset))
        StrengthSetFactory(
            workout=workout, exercise=bench, set_number=1, weight_kg=70 + offset, reps=5
        )

    spotlight = stats.exercise_spotlight(user)

    assert spotlight["exercise"] == bench
    assert spotlight["record_display"] == "83"  # 70 + 13
    assert spotlight["count_label"] == "14 тренировок"
    assert len(spotlight["sparkline"]) == stats.SPARKLINE_POINTS
    assert spotlight["sparkline"][-1] == 70.0  # свежая тренировка — минимальный offset


def test_exercise_spotlight_is_none_without_strength_records(user):
    CardioDetailsFactory(workout__user=user)

    assert stats.exercise_spotlight(user) is None
