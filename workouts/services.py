"""Логика живого режима, не привязанная к HTTP: подстановка подходов, группировка.

Отдельный модуль, чтобы правило «веса подставляются из последней тренировки
с этим упражнением» тестировалось без клиента и вьюх.
"""

from decimal import Decimal

from workouts.models import (
    MEASUREMENT_FIELDS,
    METRIC_FIELDS,
    Exercise,
    StrengthSet,
    decimal_display,
    metric_display,
    rest_display,
    ru_plural,
)


def last_sets(user, exercise):
    """Подходы упражнения из последней завершённой тренировки пользователя.

    Незавершённая текущая тренировка отфильтровывается сама: у неё duration_min IS NULL.
    Инвариант: в завершённой тренировке остаются только выполненные подходы.
    """
    last_workout_id = (
        StrengthSet.objects.filter(
            workout__user=user,
            workout__duration_min__isnull=False,
            exercise=exercise,
        )
        # Тот же тайбрейкер по id, что и в ленте истории.
        .order_by("-workout__started_at", "-workout_id")
        .values_list("workout_id", flat=True)
        .first()
    )
    if last_workout_id is None:
        return []
    return list(
        StrengthSet.objects.filter(workout_id=last_workout_id, exercise=exercise).order_by(
            "set_number"
        )
    )


def create_planned_sets(workout, exercise):
    """Плановые подходы нового упражнения: копия прошлого раза или один пустой.

    Номера проставляются заново с единицы — в источнике могли быть пропуски.
    Единицу ставим здесь явно: bulk_create не вызывает save(), а без снимка
    подход упёрся бы в set_fields_match_measurement.
    """
    previous = last_sets(workout.user, exercise)
    rows = [
        StrengthSet(
            workout=workout,
            exercise=exercise,
            set_number=number,
            measurement=exercise.measurement,
            done=False,
            **set_values(exercise.measurement, source),
        )
        for number, source in enumerate(previous, start=1)
    ] or [
        StrengthSet(
            workout=workout,
            exercise=exercise,
            set_number=1,
            measurement=exercise.measurement,
            **set_values(exercise.measurement, None),
        )
    ]
    StrengthSet.objects.bulk_create(rows)
    return rows


def set_values(measurement, source):
    """Значения подхода: применимые поля из источника, остальные — нули.

    Нули задаём явно: у weight_kg и reps нет default в модели. Источник мог быть
    записан в другой единице (упражнение переводили), поэтому берём из него
    только то, что подходит текущей единице.
    """
    values = {"weight_kg": 0, "reps": 0, "duration_sec": 0}
    if source is not None:
        values.update({field: getattr(source, field) for field in MEASUREMENT_FIELDS[measurement]})
    return values


def exercise_groups(workout):
    """Упражнения тренировки в порядке добавления, каждое со своими подходами.

    Порядок добавления восстанавливается по id подходов: отдельного поля порядка нет,
    а плановые строки создаются в момент добавления упражнения.
    """
    rows = list(workout.sets.select_related("exercise").order_by("id"))
    groups = []
    index = {}
    for row in rows:
        if row.exercise_id not in index:
            index[row.exercise_id] = len(groups)
            groups.append({"exercise": row.exercise, "sets": []})
        groups[index[row.exercise_id]]["sets"].append(row)
    for group in groups:
        group["sets"].sort(key=lambda item: item.set_number)
        # Номер на экране — позиция в списке: в set_number могут остаться пропуски.
        for position, row in enumerate(group["sets"], start=1):
            row.display_number = position
    return groups


def live_groups(workout):
    """Группы для живого экрана: у каждой статус current / queue / done и подсказка."""
    groups = exercise_groups(workout)
    pending_ids = [g["exercise"].pk for g in groups if any(not s.done for s in g["sets"])]
    current_id = None
    if pending_ids:
        # Выбранное вручную упражнение — текущее, пока у него есть невыполненные подходы.
        if workout.current_exercise_id in pending_ids:
            current_id = workout.current_exercise_id
        else:
            current_id = pending_ids[0]

    for group in groups:
        sets = group["sets"]
        group["done_sets"] = [s for s in sets if s.done]
        if group["exercise"].pk == current_id:
            group["state"] = "current"
            pending = [s for s in sets if not s.done]
            group["current_set"] = pending[0]
            # Остальные плановые подходы показываются как план под текущим.
            group["upcoming"] = pending[1:]
            group["hint"] = last_time_hint(last_sets(workout.user, group["exercise"]))
        elif group["exercise"].pk in pending_ids:
            group["state"] = "queue"
            group["hint"] = queue_hint(sets)
        else:
            group["state"] = "done"
            group["hint"] = done_hint(sets)
    return groups


def live_context(workout):
    """Контекст региона упражнений: группы, разложенные по статусам."""
    groups = live_groups(workout)
    return {
        "workout": workout,
        "current_group": next((g for g in groups if g["state"] == "current"), None),
        "queue_groups": [g for g in groups if g["state"] == "queue"],
        "done_groups": [g for g in groups if g["state"] == "done"],
    }


def set_value_hint(row):
    """Короткая запись подхода для перечисления: «77,5×8» · «8» · «1:30»."""
    measurement = row.measurement
    if measurement == Exercise.Measurement.WEIGHT_REPS:
        return f"{row.weight_display}×{row.reps}"
    if measurement == Exercise.Measurement.TIME_WEIGHT:
        return f"{rest_display(row.duration_sec)}×{row.weight_display}"
    if measurement == Exercise.Measurement.REPS:
        return str(row.reps)
    return rest_display(row.duration_sec)


def last_time_hint(previous):
    """«прошлый раз: 70×10 · 77,5×8» — или «1:00 · 1:15» у удержаний."""
    if not previous:
        return "первое выполнение"
    return "прошлый раз: " + " · ".join(set_value_hint(row) for row in previous)


def queue_hint(sets):
    """Подсказка строки очереди: «прошлый раз: 3 подхода · до 100 кг»."""
    done_count = sum(1 for s in sets if s.done)
    if done_count:
        return f"выполнено {done_count} из {len(sets)}"
    # Максимум метрики, а не веса: у планки и подтягиваний вес нулевой, и по нему
    # подсказка всегда говорила бы «первое выполнение» даже с полной историей.
    top = max(row.metric_value for row in sets)
    if not top:
        return "первое выполнение"
    count = len(sets)
    sets_word = ru_plural(count, "подход", "подхода", "подходов")
    return f"прошлый раз: {count} {sets_word} · до {metric_display(sets[0].measurement, top)}"


def done_hint(sets):
    """Итог завершённого упражнения: «3 подхода · 590 кг» или «· 4:30»."""
    count = len(sets)
    sets_word = ru_plural(count, "подход", "подхода", "подходов")
    return f"{count} {sets_word} · {exercise_total(sets)}"


def exercise_total(sets):
    """Сумма работы упражнения в его единице: тоннаж, повторы или время."""
    measurement = sets[0].measurement
    if METRIC_FIELDS[measurement] == "duration_sec":
        return rest_display(sum(row.duration_sec for row in sets))
    if measurement == Exercise.Measurement.REPS:
        total = sum(row.reps for row in sets)
        return f"{total} {ru_plural(total, 'повтор', 'повтора', 'повторов')}"
    tonnage = sum((row.tonnage_kg for row in sets), Decimal(0))
    return f"{decimal_display(tonnage)} кг"
