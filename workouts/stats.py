"""Агрегации дашборда, не привязанные к HTTP: сводка, недели, рекорды, прогресс.

Функции принимают явный `today` — так недельные и оконные расчёты тестируются
без подмены системного времени.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import NamedTuple

from django.db.models import DecimalField, ExpressionWrapper, F, FloatField, Max, Min, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import formats, timezone

from workouts.models import (
    METRIC_FIELDS,
    METRIC_LABELS,
    NO_VALUE,
    SPEED_THRESHOLD_KMH,
    CardioPart,
    Exercise,
    ExerciseNote,
    Sport,
    StrengthSet,
    Workout,
    cardio_parts_prefetch,
    decimal_display,
    metric_display,
    order_exercises,
    ru_plural,
)

SPARKLINE_POINTS = 12

# Сколько групп мышц показываем в подписи целиком, остальные сворачиваются в «+N».
# Две — предел шапки карточки на 375px: третья группа выдавливает дату.
MUSCLE_GROUPS_SHOWN = 2

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
        "days": 0,
        "first_day": None,
    }


def _split_windows(user, current_start, previous_start, last_day):
    """Итоги двух соседних окон за три запроса на оба окна.

    Раньше каждое окно считалось пятью агрегатами, и три из них ещё и
    переисполняли оконную выборку подзапросом. Тренировок за пару окон — единицы
    или сотни, поэтому дешевле забрать их строки один раз и разложить в Python;
    ту же логику уже использует weekly_chart.

    previous_start=None — прошлого окна нет; current_start=None — текущее окно
    с первой тренировки (сводка «за всё время»). Первый день с тренировкой
    текущего окна возвращается в first_day: им «всё время» подписывает начало
    без отдельного запроса.
    """
    first = previous_start or current_start
    workouts = Workout.objects.filter(user=user).finished()
    if first is None:
        workouts = workouts.filter(started_at__lt=day_bounds(last_day, last_day)[1])
    else:
        start, end = day_bounds(first, last_day)
        workouts = workouts.filter(started_at__gte=start, started_at__lt=end)
    rows = list(workouts.values_list("id", "started_at", "duration_min"))
    workout_ids = [row[0] for row in rows]
    tonnage_by_workout = dict(
        StrengthSet.objects.filter(workout_id__in=workout_ids)
        .values_list("workout_id")
        .annotate(
            # Инвариант живого режима: в завершённых тренировках только выполненные подходы.
            value=Coalesce(Sum(F("weight_kg") * F("reps"), output_field=DecimalField()), Decimal(0))
        )
    )
    # Кардио-части — отдельным запросом, а не джойном к выборке тренировок:
    # частей у тренировки может быть несколько, и джойн размножил бы строки,
    # задвоив минуты и тоннаж. Тот же довод, по которому отдельно считается
    # тоннаж.
    cardio_rows = list(
        CardioPart.objects.filter(workout_id__in=workout_ids).values_list(
            "workout_id", "sport_id", "distance_km"
        )
    )
    # Вид спорта самой тренировки здесь больше не нужен: «силовая» выводится из
    # наличия подходов, а имена для плитки дают виды спорта частей.
    sports = Sport.objects.in_bulk({sport_id for _, sport_id, _ in cardio_rows})
    # Ключи тоннажа — это и есть «у тренировки были подходы»: лишнего запроса
    # на признак силовой части не нужно.
    windows = {"current": _empty_totals(), "previous": _empty_totals()}
    cardio_names = {"current": set(), "previous": set()}
    window_by_workout = {}
    days = {"current": set(), "previous": set()}
    for workout_id, started_at, duration in rows:
        local_date = timezone.localtime(started_at).date()
        key = "current" if current_start is None or local_date >= current_start else "previous"
        window_by_workout[workout_id] = key
        days[key].add(local_date)
        totals = windows[key]
        totals["count"] += 1
        totals["minutes"] += duration
        tonnage = tonnage_by_workout.get(workout_id)
        if tonnage is not None:
            # Силовая часть есть — считаем тренировку силовой. У смешанной это
            # не мешает ей попасть и в дистанцию ниже: обе метрики законны.
            totals["tonnage"] += tonnage
            totals["strength_count"] += 1
    for workout_id, sport_id, distance in cardio_rows:
        key = window_by_workout[workout_id]
        cardio_names[key].add(sports[sport_id].name)
        if distance:
            windows[key]["distance"] += distance
    for key, names in cardio_names.items():
        windows[key]["cardio_sports"] = sorted(names)
        windows[key]["days"] = len(days[key])
        windows[key]["first_day"] = min(days[key], default=None)
    return windows


def _delta(value, suffix=""):
    """Подпись дельты плитки: «+1…», «−38 мин» или нейтральное «без изменений»."""
    if value > 0:
        return {"label": f"+{value}{suffix}", "direction": "up"}
    if value < 0:
        return {"label": f"−{abs(value)}{suffix}", "direction": "down"}
    return {"label": "без изменений", "direction": "flat"}


def _badge(current, previous, unit=""):
    """Бейдж динамики плитки на ПК: «+1», «−38 мин», «+1200 кг», без изменений — «±0».

    Короче подписи телефона (_delta): «к прошлым 7 дням» в бейдж не помещается и
    уходит в title. Когда оба окна пустые, сравнивать нечего — бейджа нет: «±0»
    у дистанции человека, который не бегает, был бы шумом на каждой загрузке.
    """
    if not current and not previous:
        return None
    value = current - previous
    text = decimal_display(abs(value)) if isinstance(value, Decimal) else str(abs(value))
    if value > 0:
        return {"label": f"+{text}{unit}", "direction": "up"}
    if value < 0:
        return {"label": f"−{text}{unit}", "direction": "down"}
    return {"label": "±0", "direction": "flat"}


class Period(NamedTuple):
    """Окно сводки дашборда: [start, end] включительно и его подписи.

    start=None — «всё время»: начало — первая записанная тренировка. Пустой
    compare_label — сравнивать не с чем: у «всего времени» прошлого окна нет.
    """

    key: str
    start: date | None
    end: date
    title: str
    compare_label: str


# Готовые окна сводки: ключ в адресе (?period=…) → чип, заголовок, подпись
# сравнения и длина в днях. Окна скользящие, а не календарные: прошлое окно той
# же длины сравнивается честно, а неполный текущий месяц с полным прошлым — нет.
PERIOD_PRESETS = {
    "7": ("7 дней", "За 7 дней", "к прошлым 7 дням", 7),
    "30": ("30 дней", "За 30 дней", "к прошлым 30 дням", 30),
    "90": ("3 месяца", "За 3 месяца", "к прошлым 3 месяцам", 90),
    "365": ("Год", "За год", "к прошлому году", 365),
    "all": ("Всё время", "За всё время", "", None),
}
DEFAULT_PERIOD = "7"
# Раньше этого дня свой период не начинается: дата из адреса недоверенная, а у
# «0001-01-01» прошлое окно ушло бы за начало календаря, и страница упала бы.
EARLIEST_DAY = date(1970, 1, 1)


def preset_period(key, today):
    _chip, title, compare_label, days = PERIOD_PRESETS[key]
    start = today - timedelta(days=days - 1) if days else None
    return Period(key, start, today, title, compare_label)


def custom_period(start, end):
    """Свой период: заголовок по числу дней — «За 12 дней», сравнение с прошлыми 12."""
    days = (end - start).days + 1
    title = f"За {days} {ru_plural(days, 'день', 'дня', 'дней')}"
    if days == 1:
        compare_label = "к прошлому дню"
    else:
        compare_label = f"к прошлым {days} {ru_plural(days, 'дню', 'дням', 'дням')}"
    return Period("custom", start, end, title, compare_label)


def range_display(start, end, today):
    """Подпись окна: «27 сен — 3 окт», «1 — 30 сен», «3 мар 2025 — 3 окт».

    Год пишется только у дат не текущего года — как в истории и на итоге.
    """

    def day(value, fmt="j b"):
        return formats.date_format(value, fmt if value.year == today.year else f"{fmt} Y")

    if start == end:
        return day(end)
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day} — {day(end)}"
    return f"{day(start)} — {day(end)}"


def seven_day_summary(user, today=None):
    """Сводка окна по умолчанию — 7 дней, как открывается дашборд."""
    today = today or timezone.localdate()
    return period_summary(user, preset_period(DEFAULT_PERIOD, today), today)


def period_summary(user, period, today=None):
    """Сводка окна дашборда с дельтами к прошлому окну той же длины.

    Дельты абсолютные («+1», «+38 мин»): окна всегда одинаковой длины, а
    деления на пустое прошлое окно просто не существует. У «всего времени»
    прошлого окна нет — нет и дельт с бейджами.
    """
    today = today or timezone.localdate()
    compare = bool(period.compare_label)
    previous_start = None
    if period.start is not None and compare:
        previous_start = period.start - timedelta(days=(period.end - period.start).days + 1)
    windows = _split_windows(user, period.start, previous_start, period.end)
    current, previous = windows["current"], windows["previous"]
    start = period.start or current["first_day"] or period.end
    length = (period.end - start).days + 1
    count_delta = current["count"] - previous["count"]
    minutes_delta = current["minutes"] - previous["minutes"]
    strength = current["strength_count"]
    count, days = current["count"], current["days"]
    # Плитки на ПК: бейджи динамики и строка под числом у тренировок и времени.
    # Всё считается из тех же двух окон — ни одного запроса сверху.
    badges = {
        "count": _badge(count, previous["count"]),
        "minutes": _badge(current["minutes"], previous["minutes"], " мин"),
        "tonnage": _badge(current["tonnage"], previous["tonnage"], " кг"),
        "distance": _badge(current["distance"], previous["distance"], " км"),
    }
    if not compare:
        # Прошлое окно здесь пусто по построению: «+12» к нулю было бы неправдой.
        badges = dict.fromkeys(badges)
    return {
        "key": period.key,
        "title": period.title,
        "compare": compare,
        "compare_label": period.compare_label,
        "start": start,
        "end": period.end,
        "range_display": range_display(start, period.end, today),
        "count": current["count"],
        "count_delta": count_delta if compare else None,
        "count_delta_label": _delta(count_delta, f" {period.compare_label}") if compare else None,
        "minutes": current["minutes"],
        "minutes_delta": minutes_delta if compare else None,
        "minutes_delta_label": _delta(minutes_delta, " мин") if compare else None,
        "duration_display": hours_display(current["minutes"]),
        "strength_count": strength,
        "strength_count_label": (
            f"{strength} {ru_plural(strength, 'силовая', 'силовые', 'силовых')}" if strength else ""
        ),
        "tonnage_display": decimal_display(current["tonnage"]),
        "distance_display": decimal_display(current["distance"]),
        "cardio_sports": current["cardio_sports"],
        "cardio_sports_label": " + ".join(name.lower() for name in current["cardio_sports"]),
        "badges": badges,
        "days_label": (
            f"{days} {ru_plural(days, 'день', 'дня', 'дней')} из {length}" if days else ""
        ),
        "average_display": hours_display(current["minutes"] // count) if count else "",
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
        .values_list("id", "started_at", "duration_min", "sport_id")
    )
    # Части — отдельным запросом: джойн размножил бы строки тренировок и
    # задвоил минуты. Тот же довод, что в _split_windows.
    part_rows = list(
        CardioPart.objects.filter(workout_id__in=[row[0] for row in rows]).values_list(
            "workout_id", "sport_id", "duration_min"
        )
    )
    parts_by_workout = {}
    for workout_id, sport_id, duration in part_rows:
        parts_by_workout.setdefault(workout_id, []).append((sport_id, duration or 0))
    sports = Sport.objects.in_bulk(
        {row[3] for row in rows} | {sport_id for _, sport_id, _ in part_rows}
    )

    index = {monday: position for position, monday in enumerate(mondays)}
    minutes = {}  # (sport_id, позиция недели) -> минуты
    totals = [0] * weeks  # минуты недели целиком — для карточки цели
    for workout_id, started_at, duration, sport_id in rows:
        # Неделя определяется по локальной дате: started_at хранится в UTC,
        # и тренировка в понедельник 00:10 МСК — это ещё воскресенье по UTC.
        monday = week_start(timezone.localtime(started_at).date())
        position = index[monday]
        totals[position] += duration
        parts = parts_by_workout.get(workout_id, [])
        for part_sport_id, part_duration in parts:
            key = (part_sport_id, position)
            minutes[key] = minutes.get(key, 0) + part_duration
        # Виду спорта-хозяину достаётся остаток: у смешанной это силовая часть,
        # у чистого кардио — ровно ноль, поэтому столбцы прежней истории не
        # сдвигаются ни на минуту. max нужен на случай, когда части длиннее
        # тренировки (правка через админку): отрицательных часов в стеке быть
        # не должно.
        rest = max(0, duration - sum(part_duration for _, part_duration in parts))
        if rest or not parts:
            key = (sport_id, position)
            minutes[key] = minutes.get(key, 0) + rest

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
        "totals": totals,
    }


def week_goal(target, week_totals, today):
    """Карточка «Цель на неделю»: прогресс календарной недели к цели в минутах.

    Минуты недель приходят из weekly_chart (последний столбец — эта неделя,
    предпоследний — прошлая): тот же счёт, что у графика рядом, и ни одного
    запроса сверху. Процент может перевалить за сотню — дуга при этом просто
    полная, а число честное.
    """
    monday = week_start(today)
    done, last = week_totals[-1], week_totals[-2]
    goal = {
        "start": monday,
        "end": monday + timedelta(days=6),
        "target": target,
        "done_display": hours_display(done),
        "last_display": hours_display(last),
    }
    if not target:
        return goal
    percent = round(done * 100 / target)
    if done >= target:
        over = done - target
        message = "Цель недели выполнена!"
        if over:
            message += f" Сверх цели — {hours_display(over)}."
    else:
        message = f"До цели осталось {hours_display(target - done)}."
    return goal | {
        "target_display": hours_display(target),
        "percent": percent,
        # Длина дуги в долях пути (pathLength="100" у SVG-полукруга).
        "arc": min(percent, 100),
        "message": message,
    }


def month_starts(today, count):
    """Первые числа последних count месяцев, от старого к текущему."""
    year, month = today.year, today.month
    starts = []
    for _ in range(count):
        starts.append(date(year, month, 1))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return starts[::-1]


def monthly_stats(user, today=None, months=12):
    """Карточка «Статистика» на ПК: время, тоннаж и дистанция по месяцам за год.

    Четыре запроса на все три вкладки сразу — тренировки, тоннаж по тренировкам,
    кардио-части и их виды спорта, — поэтому вкладки переключаются на клиенте без
    новых запросов. Время делится между хозяином и частями так же, как в
    weekly_chart, а тоннаж и части — отдельными запросами по той же причине, что
    в _split_windows: джойн размножил бы строки тренировок. Только float — по
    той же причине, что в weekly_chart.
    """
    today = today or timezone.localdate()
    starts = month_starts(today, months)
    last_day = (starts[-1] + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    first_moment, end = day_bounds(starts[0], last_day)
    index = {(start.year, start.month): position for position, start in enumerate(starts)}

    rows = list(
        Workout.objects.filter(user=user)
        .finished()
        .filter(started_at__gte=first_moment, started_at__lt=end)
        .values_list("id", "started_at", "duration_min", "sport_id")
    )
    workout_ids = [row[0] for row in rows]
    tonnage_by_workout = dict(
        StrengthSet.objects.filter(workout_id__in=workout_ids)
        .values_list("workout_id")
        .annotate(value=Sum(F("weight_kg") * F("reps"), output_field=DecimalField()))
    )
    part_rows = list(
        CardioPart.objects.filter(workout_id__in=workout_ids).values_list(
            "workout_id", "sport_id", "duration_min", "distance_km"
        )
    )
    sports = Sport.objects.in_bulk(
        {row[3] for row in rows} | {sport_id for _, sport_id, _, _ in part_rows}
    )

    position_by_workout = {}
    minutes = {}  # (sport_id, месяц) -> минуты
    tonnage = [Decimal(0)] * months
    distance = {}  # (sport_id, месяц) -> км
    parts_by_workout = {}
    for workout_id, sport_id, duration, km in part_rows:
        parts_by_workout.setdefault(workout_id, []).append((sport_id, duration or 0, km))
    for workout_id, started_at, duration, sport_id in rows:
        local = timezone.localtime(started_at).date()
        position = index[(local.year, local.month)]
        position_by_workout[workout_id] = position
        tonnage[position] += tonnage_by_workout.get(workout_id) or 0
        parts = parts_by_workout.get(workout_id, [])
        for part_sport_id, part_minutes, km in parts:
            key = (part_sport_id, position)
            minutes[key] = minutes.get(key, 0) + part_minutes
            if km:
                distance[key] = distance.get(key, Decimal(0)) + km
        rest = max(0, duration - sum(part_minutes for _, part_minutes, _ in parts))
        if rest or not parts:
            key = (sport_id, position)
            minutes[key] = minutes.get(key, 0) + rest

    ordered = sorted(sports.values(), key=lambda sport: (not sport.is_strength, sport.name))

    def series(values_by_key, convert):
        datasets = []
        for sport in ordered:
            values = [values_by_key.get((sport.pk, position), 0) for position in range(months)]
            if any(values):
                datasets.append(
                    {
                        "name": sport.name,
                        "colorKey": sport.color_key,
                        "values": [convert(value) for value in values],
                    }
                )
        return datasets

    total_minutes = sum(minutes.values())
    total_tonnage = sum(tonnage)
    total_distance = sum(distance.values(), Decimal(0))
    tonnage_dataset = {
        "name": "Тоннаж",
        "colorKey": "strength",
        "values": [round(float(value) / 1000, 2) for value in tonnage],
    }
    return {
        "labels": [formats.date_format(start, "b") for start in starts],
        "titles": [f"{formats.date_format(start, 'F')} {start.year}" for start in starts],
        "tabs": [
            {
                "key": "time",
                "title": "Время",
                "unit": "ч",
                "total": f"{round(total_minutes / 60)} ч",
                "datasets": series(minutes, lambda value: round(value / 60, 1)),
            },
            {
                "key": "tonnage",
                "title": "Тоннаж",
                "unit": "т",
                "total": f"{decimal_display(round(total_tonnage / 1000, 1))} т",
                "datasets": [tonnage_dataset] if total_tonnage else [],
            },
            {
                "key": "distance",
                "title": "Дистанция",
                "unit": "км",
                # round(…, 0), а не round(…): без знаков Decimal округляется в int.
                "total": f"{decimal_display(round(total_distance, 0))} км",
                "datasets": series(distance, lambda value: round(float(value), 1)),
            },
        ],
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

    # Метрика собирается из того, что в тренировке есть: у смешанной это и
    # тоннаж, и дистанции частей. Пустой список невозможен — тренировка без
    # подходов и без частей не записывается.
    pieces = []
    workload = workout.workload
    if workload["value"] != NO_VALUE:
        # Удержание пишется как время, и «0:49 · 1:45» читалось бы двумя
        # длительностями — ему нужна подпись. У тоннажа и повторов единица
        # уже в значении.
        if workload["label"] == "удержание":
            pieces.append(f"удержание\u00a0{workload['value']}")
        else:
            pieces.append(workload["value"])
    # Неразрывный пробел: на 375px строка переносится, и «км» не должно
    # уезжать от числа на следующую строку.
    pieces += [f"{part.distance_display}\u00a0км" for part in workout.cardio_parts.all()]
    metric = " · ".join(pieces) if pieces else NO_VALUE
    # Подходы есть — значит, у тренировки есть силовая часть: Sum по пустому
    # джойну даёт None, а по подходам планки — ноль, но не None.
    has_sets = getattr(workout, "tonnage", None) is not None
    has_cardio = bool(workout.cardio_parts.all())
    if has_sets and has_cardio:
        kind = "Смешанная"
    elif has_sets:
        kind = "Силовая"
    else:
        kind = "Кардио"
    return {
        "workout": workout,
        # Силовая подписывается группами мышц (attach_muscle_groups), иначе весь
        # блок был бы столбцом слова «Силовая». getattr, а не прямое поле: у
        # кардио групп не бывает, и подпись остаётся именем вида спорта.
        "sport_name": getattr(workout, "muscle_groups", "") or workout.sport.name,
        "color_key": workout.sport.color_key,
        "meta": f"{day_label} · {workout.duration_display} · {metric}",
        # Ячейки таблицы «Последние тренировки» на ПК — то же, что в meta, по
        # отдельности: строка одна, и телефон по-прежнему читает meta.
        "day_label": day_label,
        "metric": metric,
        "kind": kind,
    }


def latest_workouts(user, today=None, limit=5):
    """Последние завершённые тренировки для блока дашборда."""
    today = today or timezone.localdate()
    workouts = (
        Workout.objects.filter(user=user)
        .finished()
        # Место — ячейка таблицы на ПК: джойном, а не запросом на строку.
        .select_related("sport", "location")
        .prefetch_related(cardio_parts_prefetch())
        .annotate(**WORKLOAD_ANNOTATIONS)
        .order_by("-started_at", "-id")[:limit]
    )
    return [workout_row(workout, today) for workout in attach_muscle_groups(user, workouts)]


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
    # Считаем по колонкам самой части: вид спорта у неё свой, и длительность
    # своя. Из-за этого рекорд темпа у смешанной тренировки больше не может
    # оказаться ложным, а по силовому виду спорта рекорд не построится вовсе.
    rows = (
        CardioPart.objects.filter(workout__user=user, workout__duration_min__isnull=False)
        .filter(distance_km__gt=0, duration_min__gt=0)
        .values("sport_id")
        .annotate(
            max_distance=Max("distance_km"),
            best_speed=Max(
                ExpressionWrapper(
                    F("distance_km") * 60.0 / F("duration_min"),
                    output_field=FloatField(),
                )
            ),
        )
    )
    sports = Sport.objects.in_bulk([row["sport_id"] for row in rows])
    records = []
    for row in rows:
        sport = sports[row["sport_id"]]
        # Квантуем до 0,1 ДО сравнения с порогом — ровно как CardioPart.shows_speed,
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


def muscle_groups_by_workout(user, workout_ids):
    """Подпись силовой тренировки по группам мышц: {workout_id: "Грудь · Плечи"}.

    Слово «Силовая» одинаково у всех тренировок, поэтому в истории и на дашборде
    подпись собирается из содержимого: какие группы мышц были в этот раз. Здесь
    только группы; фолбэк на имя вида спорта делает шаблон — так забытая точка
    вызова даёт прежнюю подпись, а не пустоту.

    Один запрос на всю страницу, а не на карточку: иначе лента истории делала бы
    запрос на каждую тренировку. Агрегат сразу по `muscle_group`, а не выборка
    подходов, — соседние подходы одной группы для подписи не нужны.

    Порядок — общий `order_exercises`: сначала то, что реально делали (по самой
    ранней метке выполнения), потом непройденное в порядке добавления, а члены
    круга — подряд. Поэтому по `done` не фильтруем: у записанной тренировки
    невыполненных подходов уже нет, а у черновика все подходы плановые, и он
    подписывается по плану. Агрегат по упражнению, а не по группе мышц: порядок
    с кругами считается по упражнениям, и только потом группы схлопываются.
    Упражнения без группы мышц в подпись не попадают — но в сортировке
    участвуют: выкинутый до неё член круга сдвинул бы порядок блоков.
    """
    rows = (
        # Фильтр по user избыточен (id пришли из своей выборки), но правило
        # «каждый queryset пользовательских данных фильтруется по user» дороже
        # экономии одного условия — то же решение, что в exercise_positions.
        StrengthSet.objects.filter(workout_id__in=workout_ids, workout__user=user)
        .values("workout_id", "exercise_id", "exercise__muscle_group")
        .annotate(
            first_done_at=Min("done_at"),
            first_set_id=Min("id"),
            # Не «circuit»: такое имя аннотации конфликтует с полем модели.
            first_circuit=Min("circuit"),
        )
    )
    by_workout = {}
    for row in rows:
        row["circuit"] = row["first_circuit"]
        by_workout.setdefault(row["workout_id"], []).append(row)
    labels = {}
    for workout_id, items in by_workout.items():
        groups = []
        for item in order_exercises(items):
            group = item["exercise__muscle_group"]
            if group and group not in groups:
                groups.append(group)
        if not groups:
            continue
        label = " · ".join(groups[:MUSCLE_GROUPS_SHOWN])
        hidden = len(groups) - MUSCLE_GROUPS_SHOWN
        if hidden > 0:
            label = f"{label} +{hidden}"
        labels[workout_id] = label
    return labels


def attach_muscle_groups(user, workouts):
    """Проставить `muscle_groups` списку тренировок — одним запросом на всех.

    Отдельная функция, а не свойство модели: свойство читало бы подходы у каждой
    тренировки и превращало ленту в N+1.

    У смешанной к группам дописываются виды спорта её кардио-частей — «Грудь +
    Бег»: без них заезд на 34 км в ленте назывался бы «Пресс». Только когда
    части уже забраны prefetch'ем (лента, «Последние»): у одиночного экрана
    тренировки части стоят отдельным блоком, а лишний запрос сдвинул бы бюджет.
    """
    workouts = list(workouts)
    labels = muscle_groups_by_workout(user, [workout.pk for workout in workouts])
    for workout in workouts:
        label = labels.get(workout.pk, "")
        cardio = cardio_sport_names(workout)
        if label and cardio:
            label = f"{label} + {cardio}"
        workout.muscle_groups = label
    return workouts


def cardio_sport_names(workout):
    """Виды спорта кардио-частей через «·» в порядке ввода — или "" без prefetch."""
    if "cardio_parts" not in getattr(workout, "_prefetched_objects_cache", {}):
        return ""
    names = []
    for part in workout.cardio_parts.all():
        if part.sport.name not in names:
            names.append(part.sport.name)
    return " · ".join(names)


def exercise_positions(user, workout_ids, exercise):
    """Каким по счёту было упражнение в каждой из тренировок: {workout_id: номер}.

    Один запрос на всю историю, а не на тренировку: у каждой пары «тренировка +
    упражнение» берём те же два ключа, по которым сортирует
    `services.exercise_groups`, — самую раннюю метку выполнения и минимальный id
    подходов, — и считаем позицию нашего упражнения. Агрегат, а не выборка
    подходов: тянуть все подходы всех соседних упражнений было бы дороже самой
    страницы.

    Правило сортировки общее (`order_exercises`) именно поэтому: номер здесь
    обязан совпасть с номером той же тренировки на её итоге — и с кругами тоже.
    """
    rows = (
        # Фильтр по user избыточен (id пришли из своей выборки), но правило
        # «каждый queryset пользовательских данных фильтруется по user» дороже
        # экономии одного условия.
        StrengthSet.objects.filter(workout_id__in=workout_ids, workout__user=user)
        .values("workout_id", "exercise_id")
        .annotate(
            first_done_at=Min("done_at"), first_set_id=Min("id"), first_circuit=Min("circuit")
        )
    )
    by_workout = {}
    for row in rows:
        row["circuit"] = row["first_circuit"]
        by_workout.setdefault(row["workout_id"], []).append(row)
    positions = {}
    for workout_id, items in by_workout.items():
        positions[workout_id] = next(
            (
                number
                for number, item in enumerate(order_exercises(items), start=1)
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
        # workout__location — для разреза по местам: один LEFT JOIN в тот же
        # запрос, поэтому сравнение по залам не стоит ни одного нового.
        .select_related("workout", "workout__location")
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


def progress_by_location(exercise, progress):
    """Разрез прогресса по местам: рекорд и число тренировок в каждом.

    Считается в Python по уже загруженным группам — тот же приём, что у
    group_by_muscle и services.exercise_groups: отдельных запросов это не стоит,
    место пришло вместе с тренировкой в select_related.

    Порядок — по рекорду, а без метрики (собственный вес) по числу тренировок:
    иначе подтягивания встали бы в конец по алфавиту случайного места.
    """
    buckets = {}
    for group in progress:
        location = group["workout"].location
        key = location.pk if location is not None else None
        bucket = buckets.setdefault(
            key,
            # «Место не указано» — такой же разрез, как остальные: у истории до
            # появления справочника места нет, и прятать её было бы враньём.
            {
                "name": location.name if location else "Место не указано",
                "max_value": 0.0,
                "count": 0,
            },
        )
        bucket["count"] += 1
        bucket["max_value"] = max(bucket["max_value"], group["max_value"])
    rows = sorted(buckets.values(), key=lambda row: (-row["max_value"], -row["count"], row["name"]))
    for row in rows:
        row["max_display"] = (
            metric_display(exercise.measurement, row["max_value"]) if row["max_value"] else ""
        )
        row["count_label"] = (
            f"{row['count']} {ru_plural(row['count'], 'тренировка', 'тренировки', 'тренировок')}"
        )
    # Один разрез — это не сравнение, а повтор строки статистики выше.
    return rows if len(rows) > 1 else []


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
