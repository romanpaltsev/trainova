"""Агрегации дашборда, не привязанные к HTTP: сводка, недели, рекорды, прогресс.

Функции принимают явный `today` — так недельные и оконные расчёты тестируются
без подмены системного времени.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, FloatField, Max, Sum
from django.db.models.functions import Coalesce
from django.utils import formats, timezone

from workouts.models import (
    SPEED_THRESHOLD_KMH,
    CardioDetails,
    Exercise,
    Sport,
    StrengthSet,
    Workout,
    decimal_display,
)
from workouts.services import ru_plural

SPARKLINE_POINTS = 12


def week_start(date):
    """Понедельник недели, к которой относится дата."""
    return date - timedelta(days=date.weekday())


def week_title(start, today):
    """Заголовок недели: «Эта неделя», «Прошлая неделя» или диапазон дат."""
    current = week_start(today)
    if start == current:
        return "Эта неделя"
    if start == current - timedelta(days=7):
        return "Прошлая неделя"
    end = start + timedelta(days=6)
    return f"{start:%d.%m} — {end:%d.%m}"


def day_bounds(first_day, last_day):
    """Aware-границы диапазона локальных суток: [first_day 00:00, last_day+1 00:00)."""
    start = timezone.make_aware(datetime.combine(first_day, time.min))
    end = timezone.make_aware(datetime.combine(last_day + timedelta(days=1), time.min))
    return start, end


def hours_display(minutes):
    """Минуты как 4:05 — в том же формате, что Workout.duration_display."""
    hours, rest = divmod(minutes, 60)
    return f"{hours}:{rest:02d}"


def _window_totals(user, first_day, last_day):
    start, end = day_bounds(first_day, last_day)
    workouts = (
        Workout.objects.filter(user=user)
        .finished()
        .filter(started_at__gte=start, started_at__lt=end)
    )
    base = workouts.aggregate(count=Count("id"), minutes=Coalesce(Sum("duration_min"), 0))
    strength_count = workouts.filter(sport__category=Sport.Category.STRENGTH).count()
    # Инвариант живого режима: в завершённых тренировках только выполненные подходы.
    tonnage = StrengthSet.objects.filter(workout__in=workouts).aggregate(
        value=Coalesce(Sum(F("weight_kg") * F("reps"), output_field=DecimalField()), Decimal(0))
    )["value"]
    distance = CardioDetails.objects.filter(workout__in=workouts).aggregate(
        value=Coalesce(Sum("distance_km"), Decimal(0))
    )["value"]
    cardio_sports = list(
        Sport.objects.filter(workouts__in=workouts, category=Sport.Category.CARDIO)
        .distinct()
        .order_by("name")
        .values_list("name", flat=True)
    )
    return {
        "count": base["count"],
        "minutes": base["minutes"],
        "strength_count": strength_count,
        "tonnage": tonnage,
        "distance": distance,
        "cardio_sports": cardio_sports,
    }


def _delta(value, suffix=""):
    """Подпись дельты плитки: «+1…», «−38 мин» или нейтральное «без изменений»."""
    if value > 0:
        return {"label": f"+{value}{suffix}", "direction": "up"}
    if value < 0:
        return {"label": f"−{abs(value)}{suffix}", "direction": "down"}
    return {"label": "без изменений", "direction": "flat"}


def seven_day_summary(user, today=None):
    """Сводка скользящего окна «за 7 дней» с дельтами к прошлым 7 дням.

    Дельты абсолютные («+1», «+38 мин»): окна всегда одинаковой длины,
    а деления на пустую прошлую неделю просто не существует.
    """
    today = today or timezone.localdate()
    current = _window_totals(user, today - timedelta(days=6), today)
    previous = _window_totals(user, today - timedelta(days=13), today - timedelta(days=7))
    count_delta = current["count"] - previous["count"]
    minutes_delta = current["minutes"] - previous["minutes"]
    strength = current["strength_count"]
    return {
        "start": today - timedelta(days=6),
        "end": today,
        "count": current["count"],
        "count_delta": count_delta,
        "count_delta_label": _delta(count_delta, " к прошлым 7 дням"),
        "minutes": current["minutes"],
        "minutes_delta": minutes_delta,
        "minutes_delta_label": _delta(minutes_delta, " мин"),
        "duration_display": hours_display(current["minutes"]),
        "strength_count": strength,
        "strength_count_label": (
            f"{strength} {ru_plural(strength, 'силовая', 'силовые', 'силовых')}" if strength else ""
        ),
        "tonnage_display": decimal_display(current["tonnage"]),
        "distance_display": decimal_display(current["distance"]),
        "cardio_sports": current["cardio_sports"],
        "cardio_sports_label": " + ".join(name.lower() for name in current["cardio_sports"]),
    }


def weekly_chart(user, today=None, weeks=12):
    """Данные stacked bar «часы по неделям»: только float — json_script
    сериализует Decimal строками и молча ломает математику на клиенте."""
    today = today or timezone.localdate()
    last_monday = week_start(today)
    mondays = [last_monday - timedelta(weeks=offset) for offset in range(weeks - 1, -1, -1)]
    start, end = day_bounds(mondays[0], last_monday + timedelta(days=6))

    rows = list(
        Workout.objects.filter(user=user)
        .finished()
        .filter(started_at__gte=start, started_at__lt=end)
        .values_list("started_at", "duration_min", "sport_id")
    )
    sports = Sport.objects.in_bulk({sport_id for _, _, sport_id in rows})

    index = {monday: position for position, monday in enumerate(mondays)}
    minutes = {}  # (sport_id, позиция недели) -> минуты
    for started_at, duration, sport_id in rows:
        # Неделя определяется по локальной дате: started_at хранится в UTC,
        # и тренировка в понедельник 00:10 МСК — это ещё воскресенье по UTC.
        monday = week_start(timezone.localtime(started_at).date())
        key = (sport_id, index[monday])
        minutes[key] = minutes.get(key, 0) + duration

    ordered = sorted(sports.values(), key=lambda sport: (not sport.is_strength, sport.name))
    datasets = [
        {
            "name": sport.name,
            "colorKey": sport.color_key,
            "hours": [
                round(minutes.get((sport.pk, position), 0) / 60, 2) for position in range(weeks)
            ],
        }
        for sport in ordered
    ]
    return {
        "labels": [f"{monday:%d.%m}" for monday in mondays],
        "starts": [monday.isoformat() for monday in mondays],
        "titles": [week_title(monday, today) for monday in mondays],
        "datasets": datasets,
    }


def workout_row(workout, today):
    """Строка тренировки для дашборда: «вчера · 1:02 · 7240 кг».

    Тоннаж ожидается аннотацией queryset'а (как в ленте истории) — без неё
    строка силовой обошлась бы отдельным запросом на карточку.
    """
    local_date = timezone.localtime(workout.started_at).date()
    if local_date == today:
        day_label = "сегодня"
    elif local_date == today - timedelta(days=1):
        day_label = "вчера"
    elif today - local_date <= timedelta(days=6):
        day_label = formats.date_format(local_date, "D").lower()
    else:
        day_label = formats.date_format(local_date, "j b")

    if workout.sport.is_strength:
        tonnage = getattr(workout, "tonnage", None) or 0
        metric = f"{decimal_display(Decimal(tonnage))} кг"
    else:
        metric = f"{workout.cardio.distance_display} км"
    return {
        "workout": workout,
        "sport_name": workout.sport.name,
        "color_key": workout.sport.color_key,
        "meta": f"{day_label} · {workout.duration_display} · {metric}",
    }


def latest_workouts(user, today=None, limit=5):
    """Последние завершённые тренировки для блока дашборда."""
    today = today or timezone.localdate()
    workouts = (
        Workout.objects.filter(user=user)
        .finished()
        .select_related("sport", "cardio")
        .annotate(tonnage=Sum(F("sets__weight_kg") * F("sets__reps")))
        .order_by("-started_at", "-id")[:limit]
    )
    return [workout_row(workout, today) for workout in workouts]


def strength_records(user, limit=None):
    """Максимальный вес по упражнениям; вес 0 (упражнения без веса) — не рекорд."""
    rows = (
        StrengthSet.objects.filter(
            workout__user=user,
            # Явный аналог .finished(): плановые подходы активной тренировки
            # копируют прошлые веса и рекордами быть не должны.
            workout__duration_min__isnull=False,
            weight_kg__gt=0,
        )
        .values("exercise_id", "exercise__name")
        .annotate(weight=Max("weight_kg"))
        .order_by("-weight", "exercise__name")
    )
    if limit is not None:
        rows = rows[:limit]
    return [
        {
            "exercise_id": row["exercise_id"],
            "name": row["exercise__name"],
            "weight": float(row["weight"]),
            "weight_display": decimal_display(row["weight"]),
        }
        for row in rows
    ]


def cardio_records(user):
    """Рекорды кардио по видам: максимальная дистанция и лучший темп.

    Темп = 3600 / скорость, поэтому максимум скорости и лучший темп — одна и та же
    тренировка: хватает одной агрегации, а порог решает, в чём показывать.
    """
    rows = (
        Workout.objects.filter(user=user)
        .finished()
        .filter(cardio__distance_km__gt=0, duration_min__gt=0)
        .values("sport_id")
        .annotate(
            max_distance=Max("cardio__distance_km"),
            best_speed=Max(
                ExpressionWrapper(
                    F("cardio__distance_km") * 60.0 / F("duration_min"),
                    output_field=FloatField(),
                )
            ),
        )
    )
    sports = Sport.objects.in_bulk([row["sport_id"] for row in rows])
    records = []
    for row in rows:
        sport = sports[row["sport_id"]]
        speed = row["best_speed"]
        if Decimal(str(speed)) >= SPEED_THRESHOLD_KMH:
            metric_label = "скорость"
            metric_display = f"{speed:.1f} км/ч".replace(".", ",")
        else:
            pace_min, pace_sec = divmod(int(3600 / speed), 60)
            metric_label = "темп"
            metric_display = f"{pace_min}:{pace_sec:02d} /км"
        records.append(
            {
                "sport_id": sport.pk,
                "name": sport.name,
                "color_key": sport.color_key,
                "max_distance_km": float(row["max_distance"]),
                "distance_display": decimal_display(row["max_distance"]),
                "metric_label": metric_label,
                "metric_display": metric_display,
            }
        )
    records.sort(key=lambda record: record["name"])
    return records


def exercise_progress(user, exercise):
    """Прогресс упражнения по завершённым тренировкам пользователя.

    Фильтр по user и делает страницу глобального упражнения персональной.
    """
    rows = (
        StrengthSet.objects.filter(
            exercise=exercise, workout__user=user, workout__duration_min__isnull=False
        )
        .select_related("workout")
        .order_by("workout__started_at", "workout_id", "set_number")
    )
    progress = []
    seen = {}
    for row in rows:
        if row.workout_id not in seen:
            local_date = timezone.localtime(row.workout.started_at).date()
            seen[row.workout_id] = len(progress)
            progress.append(
                {
                    "workout": row.workout,
                    "date": local_date,
                    "label": f"{local_date:%d.%m}",
                    "max_weight": 0.0,
                    "sets": [],
                }
            )
        group = progress[seen[row.workout_id]]
        group["sets"].append(row)
        group["max_weight"] = max(group["max_weight"], float(row.weight_kg))
    for group in progress:
        group["max_weight_display"] = decimal_display(Decimal(str(group["max_weight"])))
    return progress


def exercise_spotlight(user):
    """Карточка-прожектор: топ-упражнение по рекорду со спарклайном веса."""
    top = strength_records(user, limit=1)
    if not top:
        return None
    exercise = Exercise.objects.get(pk=top[0]["exercise_id"])
    progress = exercise_progress(user, exercise)
    return {
        "exercise": exercise,
        "record_display": top[0]["weight_display"],
        "count_label": (
            f"{len(progress)} {ru_plural(len(progress), 'тренировка', 'тренировки', 'тренировок')}"
        ),
        "sparkline": [group["max_weight"] for group in progress[-SPARKLINE_POINTS:]],
    }
