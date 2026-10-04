"""Корзина тренировок: снимок при удалении, восстановление, чистка.

Почему снимок, а не флаг «удалено», — в докстринге DeletedWorkout. Снимок — это
строки тренировки, подходов, кардио-частей и заметок в сериализаторе Django
"python" с исходными pk плюс имена справочников. Восстановление вставляет строки
обратно с теми же pk: старые ссылки и открытые вкладки снова работают, а порядок
упражнений по min(id) подходов и порядок частей по id совпадают с исходными, —
это настоящая отмена, а не копия.
"""

from django.core import serializers
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from workouts import services
from workouts.models import (
    TRASH_RETENTION,
    CardioPart,
    DeletedWorkout,
    Exercise,
    ExerciseNote,
    Location,
    Sport,
    StrengthSet,
    Workout,
)

# Метка сообщения «удалено» с кнопкой «Восстановить» (extra_tags): номер
# снимка идёт после префикса. Текст сообщения остаётся простым текстом.
RESTORE_TAG = "restore:"


class RestoreRefused(Exception):
    """Восстановить нельзя; текст для человека — в str(error)."""


def cutoff(now=None):
    """Граница корзины: удалённое раньше этого момента уже не восстанавливается."""
    return (now or timezone.now()) - TRASH_RETENTION


def recent_for(user):
    """Корзина пользователя за последние 30 дней — без тяжёлого снимка."""
    return DeletedWorkout.objects.filter(user=user, deleted_at__gte=cutoff()).defer("payload")


def expired():
    """Всё, что пролежало дольше срока, — у всех пользователей (для чистки)."""
    return DeletedWorkout.objects.filter(deleted_at__lt=cutoff())


def restore_tag(entry):
    return f"{RESTORE_TAG}{entry.pk}" if entry is not None else ""


def _snapshot(workout, sets, parts, notes):
    exercises = {row.exercise_id: row.exercise for row in sets}
    exercises.update({note.exercise_id: note.exercise for note in notes})
    if workout.current_exercise_id and workout.current_exercise_id not in exercises:
        current = Exercise.objects.filter(pk=workout.current_exercise_id).first()
        if current is not None:
            exercises[current.pk] = current
    sports = {workout.sport_id: workout.sport}
    sports.update({part.sport_id: part.sport for part in parts})
    locations = {workout.location_id: workout.location} if workout.location_id else {}
    return {
        "v": 1,
        "objects": serializers.serialize("python", [workout, *sets, *parts, *notes]),
        # Имена и атрибуты на случай, если справочник к восстановлению удалят:
        # тогда запись найдётся или заведётся заново по имени — тот же контракт
        # «совпало имя — это оно», что у импорта Excel. Ключи — строки: JSON.
        "refs": {
            "exercises": {
                str(pk): {
                    "name": item.name,
                    "measurement": item.measurement,
                    "muscle_group": item.muscle_group,
                    "equipment": item.equipment,
                }
                for pk, item in exercises.items()
            },
            "sports": {
                str(pk): {"name": item.name, "category": item.category}
                for pk, item in sports.items()
            },
            "locations": {str(pk): {"name": item.name} for pk, item in locations.items()},
        },
    }


def move_to_trash(workout, *, title, subtitle):
    """Снимок тренировки в корзину и удаление её строк. Возвращает снимок.

    Строка тренировки перечитывается под блокировкой: дважды отправленная
    форма удаления иначе положила бы в корзину два снимка одной тренировки.
    Второй запрос дождётся первого и найдёт пустоту — тогда None.
    """
    with transaction.atomic():
        locked = (
            Workout.objects.select_for_update(of=("self",))
            .select_related("sport", "location")
            .filter(pk=workout.pk, user_id=workout.user_id)
            .first()
        )
        if locked is None:
            return None
        sets = list(locked.sets.select_related("exercise").order_by("id"))
        parts = list(locked.cardio_parts.select_related("sport").order_by("id"))
        notes = list(locked.exercise_notes.select_related("exercise").order_by("id"))
        entry = DeletedWorkout.objects.create(
            user_id=locked.user_id,
            title=title,
            subtitle=subtitle,
            color_key=locked.sport.color_key,
            payload=_snapshot(locked, sets, parts, notes),
        )
        locked.delete()
    return entry


def _resolve(ids, found, refs, create):
    """Исходный id → id записи сейчас: по id, а пропавшее — по имени."""
    mapping = {}
    for source_id in ids:
        if source_id in found:
            mapping[source_id] = source_id
            continue
        ref = refs.get(str(source_id))
        if ref is None:
            raise RestoreRefused("Не получилось восстановить: снимок тренировки неполный.")
        mapping[source_id] = create(ref).pk
    return mapping


def _resolve_catalogs(user, workout, sets, parts, notes, refs):
    exercise_ids = {row.exercise_id for row in sets} | {note.exercise_id for note in notes}
    exercises = _resolve(
        exercise_ids,
        Exercise.objects.visible_to(user).in_bulk(exercise_ids),
        refs.get("exercises", {}),
        lambda ref: services.exercise_for_name(
            user,
            ref["name"],
            measurement=ref.get("measurement"),
            muscle_group=ref.get("muscle_group", ""),
            equipment=ref.get("equipment", ""),
        ),
    )
    sport_ids = {workout.sport_id} | {part.sport_id for part in parts}
    sports = _resolve(
        sport_ids,
        Sport.objects.visible_to(user).in_bulk(sport_ids),
        refs.get("sports", {}),
        lambda ref: services.sport_for_name(user, ref["name"], category=ref["category"]),
    )
    location_ids = {workout.location_id} - {None}
    locations = _resolve(
        location_ids,
        Location.objects.filter(owner=user).in_bulk(location_ids),
        refs.get("locations", {}),
        lambda ref: services.location_for_name(user, ref["name"]),
    )
    return exercises, sports, locations


def _relink(user, workout, sets, parts, notes, refs):
    """Переставить ссылки восстановленных строк на записи, которые есть сейчас."""
    exercises, sports, locations = _resolve_catalogs(user, workout, sets, parts, notes, refs)
    workout.user_id = user.pk
    workout.sport_id = sports[workout.sport_id]
    workout.location_id = locations.get(workout.location_id)
    by_target = {}
    for row in sorted(sets, key=lambda item: item.pk):
        row.exercise_id = exercises[row.exercise_id]
        by_target.setdefault(row.exercise_id, []).append(row)
    # Два исходных упражнения сошлись в одно (своё «Жим» удалили, а общее с тем
    # же именем есть): номера подходов перенумеровываются, иначе упёрлись бы в
    # unique_set_number_per_exercise.
    for rows in by_target.values():
        if len({row.set_number for row in rows}) != len(rows):
            for number, row in enumerate(sorted(rows, key=lambda r: r.pk), start=1):
                row.set_number = number
    current = exercises.get(workout.current_exercise_id)
    workout.current_exercise_id = current if current in by_target else None
    for part in parts:
        part.sport_id = sports[part.sport_id]
    kept_notes, seen = [], set()
    for note in sorted(notes, key=lambda item: item.pk):
        note.exercise_id = exercises[note.exercise_id]
        if note.exercise_id not in seen:
            seen.add(note.exercise_id)
            kept_notes.append(note)
    return kept_notes


def restore(pk, user):
    """Вернуть тренировку из корзины. None — снимка нет (чужой или уже вернули).

    Отказы с понятной причиной — RestoreRefused: срок вышел или уже идёт другая
    тренировка (идущую нельзя вернуть второй — unique_live_workout_per_user, а
    превращать её в записанную значило бы выдумать длительность).
    """
    entry = DeletedWorkout.objects.select_for_update().filter(user=user, pk=pk).first()
    if entry is None:
        return None
    if entry.deleted_at < cutoff():
        raise RestoreRefused("Срок вышел: тренировки дольше 30 дней в корзине не хранятся.")
    objects = [
        item.object
        for item in serializers.deserialize(
            "python", entry.payload["objects"], ignorenonexistent=True
        )
    ]
    workout = next(item for item in objects if isinstance(item, Workout))
    sets = [item for item in objects if isinstance(item, StrengthSet)]
    parts = [item for item in objects if isinstance(item, CardioPart)]
    notes = [item for item in objects if isinstance(item, ExerciseNote)]
    is_live = workout.started_at is not None and workout.duration_min is None
    if is_live and Workout.objects.filter(user=user).live().exists():
        raise RestoreRefused("Сначала завершите текущую тренировку.")
    try:
        # Одна точка отката: справочники, заведённые по имени, уходят вместе с
        # неудачной вставкой.
        with transaction.atomic():
            notes = _relink(user, workout, sets, parts, notes, entry.payload.get("refs", {}))
            workout.save(force_insert=True)
            StrengthSet.objects.bulk_create(sets)
            CardioPart.objects.bulk_create(parts)
            ExerciseNote.objects.bulk_create(notes)
            entry.delete()
    except IntegrityError as error:
        raise RestoreRefused("Не получилось восстановить: тренировка уже на месте.") from error
    workout.has_sets = bool(sets)
    return workout


def restored_url(workout):
    """Дом восстановленной тренировки: итог, живой экран или форма кардио.

    Итог безопасен для любой записанной (без подходов он сам уводит в форму);
    живой экран — только силовым и тем, где есть подходы: кардио-черновик там
    получил бы 404.
    """
    if workout.duration_min is not None:
        return reverse("workout_summary", args=[workout.pk])
    if workout.has_sets or workout.sport.is_strength:
        return reverse("workout_live", args=[workout.pk])
    return reverse("workout_edit", args=[workout.pk])
