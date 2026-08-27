"""Логика живого режима, не привязанная к HTTP: подстановка подходов, группировка.

Отдельный модуль, чтобы правило «веса подставляются из последней тренировки
с этим упражнением» тестировалось без клиента и вьюх.
"""

from decimal import Decimal

from workouts.models import StrengthSet, decimal_display


def ru_plural(number, one, few, many):
    """Русское склонение: 1 подход, 2 подхода, 5 подходов."""
    tail = abs(number) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


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
    """Плановые подходы нового упражнения: копия прошлого раза или один пустой 0×0.

    Номера проставляются заново с единицы — в источнике могли быть пропуски.
    """
    previous = last_sets(workout.user, exercise)
    rows = [
        StrengthSet(
            workout=workout,
            exercise=exercise,
            set_number=number,
            weight_kg=source.weight_kg,
            reps=source.reps,
            done=False,
        )
        for number, source in enumerate(previous, start=1)
    ] or [StrengthSet(workout=workout, exercise=exercise, set_number=1, weight_kg=0, reps=0)]
    StrengthSet.objects.bulk_create(rows)
    return rows


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


def last_time_hint(previous):
    """«прошлый раз: 70×10 · 77,5×8 · 80×5» под заголовком текущего упражнения."""
    if not previous:
        return "первое выполнение"
    return "прошлый раз: " + " · ".join(f"{s.weight_display}×{s.reps}" for s in previous)


def queue_hint(sets):
    """Подсказка строки очереди: «прошлый раз: 3 подхода · до 100 кг»."""
    done_count = sum(1 for s in sets if s.done)
    if done_count:
        return f"выполнено {done_count} из {len(sets)}"
    top_weight = max(Decimal(str(s.weight_kg)) for s in sets)
    if not top_weight:
        return "первое выполнение"
    count = len(sets)
    sets_word = ru_plural(count, "подход", "подхода", "подходов")
    return f"прошлый раз: {count} {sets_word} · до {decimal_display(top_weight)} кг"


def done_hint(sets):
    """Итог завершённого упражнения: «3 подхода · 590 кг»."""
    tonnage = sum((s.tonnage_kg for s in sets), Decimal(0))
    count = len(sets)
    sets_word = ru_plural(count, "подход", "подхода", "подходов")
    return f"{count} {sets_word} · {decimal_display(tonnage)} кг"
