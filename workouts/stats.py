"""Агрегации дашборда, не привязанные к HTTP: сводка, недели, рекорды, прогресс.

Функции принимают явный `today` — так недельные и оконные расчёты тестируются
без подмены системного времени.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, FloatField, Max, Min, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import formats, timezone

from workouts.models import (
    METRIC_FIELDS,
    METRIC_LABELS,
    SPEED_THRESHOLD_KMH,
    Exercise,
    ExerciseNote,
    Sport,
    StrengthSet,
    Workout,
    decimal_display,
    exercise_order_key,
    metric_display,
    ru_plural,
)

SPARKLINE_POINTS = 12

# Аннотации для `Workout.workload`: все три суммы идут по одному join'у на подходы,
# поэтому считаются одним запросом и не размножают строки друг друга. Повторы
# суммируются только у повторных упражнений — в весовых они уже вошли в тоннаж.
# Порядок единиц в блоке «Личные рекорды»: сначала весовая работа (главная метрика
# зала), потом удержания, потом повторы. Внутри единицы сортирует само значение.
RECORD_ORDER = (
    Exercise.Measurement.WEIGHT_REPS,
    Exercise.Measurement.TIME_WEIGHT,
    Exercise.Measurement.TIME,
    Exercise.Measurement.REPS,
)

WORKLOAD_ANNOTATIONS = {
    "tonnage": Sum(F("sets__weight_kg") * F("sets__reps")),
    "total_reps": Sum("sets__reps", filter=Q(sets__measurement=Exercise.Measurement.REPS)),
    "total_duration": Sum("sets__duration_sec"),
}


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


def _empty_totals():
    return {
        "count": 0,
        "minutes": 0,
        "strength_count": 0,
        "tonnage": Decimal(0),
        "distance": Decimal(0),
        "cardio_sports": [],
    }


def _split_windows(user, current_start, previous_start, last_day):
    """Итоги двух соседних недельных окон за три запроса на оба окна.

    Раньше каждое окно считалось пятью агрегатами, и три из них ещё и
    переисполняли оконную выборку подзапросом. Тренировок за две недели — единицы,
    поэтому дешевле забрать их строки один раз и разложить в Python; ту же логику
    уже использует weekly_chart.
    """
    start, end = day_bounds(previous_start, last_day)
    rows = list(
        Workout.objects.filter(user=user)
        .finished()
        .filter(started_at__gte=start, started_at__lt=end)
        .values_list("id", "started_at", "duration_min", "sport_id", "cardio__distance_km")
    )
    sports = Sport.objects.in_bulk({sport_id for *_, sport_id, _ in rows})
    tonnage_by_workout = dict(
        StrengthSet.objects.filter(workout_id__in=[row[0] for row in rows])
        .values_list("workout_id")
        .annotate(
            # Инвариант живого режима: в завершённых тренировках только выполненные подходы.
            value=Coalesce(Sum(F("weight_kg") * F("reps"), output_field=DecimalField()), Decimal(0))
        )
    )

    windows = {"current": _empty_totals(), "previous": _empty_totals()}
    cardio_names = {"current": set(), "previous": set()}
    for workout_id, started_at, duration, sport_id, distance in rows:
        local_date = timezone.localtime(started_at).date()
        key = "current" if local_date >= current_start else "previous"
        totals = windows[key]
        totals["count"] += 1
        totals["minutes"] += duration
        totals["tonnage"] += tonnage_by_workout.get(workout_id, Decimal(0))
        sport = sports[sport_id]
        if sport.is_strength:
            totals["strength_count"] += 1
        else:
            cardio_names[key].add(sport.name)
            if distance:
                totals["distance"] += distance
    for key, names in cardio_names.items():
        windows[key]["cardio_sports"] = sorted(names)
    return windows


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
    windows = _split_windows(user, today - timedelta(days=6), today - timedelta(days=13), today)
    current, previous = windows["current"], windows["previous"]
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

    Метрика нагрузки ожидается аннотациями queryset'а (WORKLOAD_ANNOTATIONS) —
    без них строка силовой обошлась бы отдельным запросом на карточку.
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
        metric = workout.workload["value"]
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
        .annotate(**WORKLOAD_ANNOTATIONS)
        .order_by("-started_at", "-id")[:limit]
    )
    return [workout_row(workout, today) for workout in workouts]


def strength_records(user, limit=None):
    """Рекорд каждого упражнения в его единице: вес, повторы или удержание.

    Один запрос на все единицы: тянем три максимума, а метрику выбираем в Python.
    Нулевая метрика рекордом не считается — упражнение просто ещё не выполняли.
    """
    rows = (
        StrengthSet.objects.filter(
            workout__user=user,
            # Явный аналог .finished(): плановые подходы активной тренировки
            # копируют прошлые значения и рекордами быть не должны.
            workout__duration_min__isnull=False,
            # Рекорд — в той единице, в которой упражнение измеряется сейчас:
            # иначе у переведённого упражнения нашлось бы два рекорда сразу.
            measurement=F("exercise__measurement"),
        )
        .values("exercise_id", "exercise__name", "measurement")
        .annotate(
            top_weight=Max("weight_kg"),
            top_reps=Max("reps"),
            top_duration=Max("duration_sec"),
        )
    )
    records = []
    for row in rows:
        measurement = row["measurement"]
        value = {
            "weight_kg": row["top_weight"],
            "reps": row["top_reps"],
            "duration_sec": row["top_duration"],
        }[METRIC_FIELDS[measurement]]
        if not value:
            continue
        records.append(
            {
                "exercise_id": row["exercise_id"],
                "name": row["exercise__name"],
                "measurement": measurement,
                "metric_label": METRIC_LABELS[measurement],
                "value": float(value),
                "value_display": metric_display(measurement, value),
            }
        )
    # Сравнивать 100 кг с 90 секундами бессмысленно, поэтому сначала приоритет
    # единицы, а внутри единицы — само значение.
    records.sort(key=lambda r: (RECORD_ORDER.index(r["measurement"]), -r["value"], r["name"]))
    return records[:limit] if limit is not None else records


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
        # Квантуем до 0,1 ДО сравнения с порогом — ровно как CardioDetails.shows_speed,
        # иначе на границе (13,95…14,0) карточка и рекорд разошлись бы в юнитах.
        speed = Decimal(str(row["best_speed"])).quantize(Decimal("0.1"))
        if speed >= SPEED_THRESHOLD_KMH:
            metric_label = "скорость"
            metric_display = f"{speed} км/ч".replace(".", ",")
        else:
            pace_min, pace_sec = divmod(int(3600 / row["best_speed"]), 60)
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


def exercise_positions(user, workout_ids, exercise):
    """Каким по счёту было упражнение в каждой из тренировок: {workout_id: номер}.

    Один запрос на всю историю, а не на тренировку: у каждой пары «тренировка +
    упражнение» берём те же два ключа, по которым сортирует
    `services.exercise_groups`, — самую раннюю метку выполнения и минимальный id
    подходов, — и считаем позицию нашего упражнения. Агрегат, а не выборка
    подходов: тянуть все подходы всех соседних упражнений было бы дороже самой
    страницы.

    Правило сортировки общее (`exercise_order_key`) именно поэтому: номер здесь
    обязан совпасть с номером той же тренировки на её итоге.
    """
    rows = (
        # Фильтр по user избыточен (id пришли из своей выборки), но правило
        # «каждый queryset пользовательских данных фильтруется по user» дороже
        # экономии одного условия.
        StrengthSet.objects.filter(workout_id__in=workout_ids, workout__user=user)
        .values("workout_id", "exercise_id")
        .annotate(first_done_at=Min("done_at"), first_set_id=Min("id"))
    )
    by_workout = {}
    for row in rows:
        by_workout.setdefault(row["workout_id"], []).append(row)
    positions = {}
    for workout_id, items in by_workout.items():
        items.sort(key=lambda item: exercise_order_key(item["first_done_at"], item["first_set_id"]))
        positions[workout_id] = next(
            (
                number
                for number, item in enumerate(items, start=1)
                if item["exercise_id"] == exercise.pk
            ),
            None,
        )
    return positions


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
                    "max_value": 0.0,
                    "sets": [],
                }
            )
        group = progress[seen[row.workout_id]]
        group["sets"].append(row)
        group["max_value"] = max(group["max_value"], float(row.metric_value))
    # Заметки одним запросом на всю историю упражнения, а не по тренировке.
    notes = (
        dict(
            ExerciseNote.objects.filter(exercise=exercise, workout__user=user).values_list(
                "workout_id", "text"
            )
        )
        if progress
        else {}
    )
    # Номера — тем же приёмом, что заметки: один запрос на всю историю.
    positions = exercise_positions(user, list(seen), exercise) if progress else {}
    for group in progress:
        # Метрика берётся у упражнения, а не у подхода: если упражнение перевели
        # в другую единицу, график должен говорить на одном языке.
        group["max_value_display"] = metric_display(exercise.measurement, group["max_value"])
        group["note"] = notes.get(group["workout"].pk, "")
        # Каким по счёту это упражнение было в той тренировке.
        group["position"] = positions.get(group["workout"].pk)
    return progress


def exercise_spotlight(user, records=None):
    """Карточка-прожектор: топ-упражнение по рекорду со спарклайном веса.

    `records` можно передать готовыми: дашборд всё равно считает рекорды
    для своего блока, и второй скан всех подходов там был лишним.
    """
    top = records if records is not None else strength_records(user, limit=1)
    if not top:
        return None
    exercise = Exercise.objects.get(pk=top[0]["exercise_id"])
    # Максимум метрики по тренировке одним запросом: тянуть всю историю подходов
    # ради 12 точек спарклайна незачем.
    rows = (
        StrengthSet.objects.filter(
            exercise=exercise, workout__user=user, workout__duration_min__isnull=False
        )
        .values_list("workout_id")
        .annotate(
            top_value=Max(METRIC_FIELDS[exercise.measurement]),
            started_at=Max("workout__started_at"),
        )
        .order_by("started_at")
    )
    values = [float(row[1]) for row in rows]
    return {
        "exercise": exercise,
        "record_display": top[0]["value_display"],
        "metric_label": top[0]["metric_label"],
        "count_label": (
            f"{len(values)} {ru_plural(len(values), 'тренировка', 'тренировки', 'тренировок')}"
        ),
        "sparkline": values[-SPARKLINE_POINTS:],
    }
