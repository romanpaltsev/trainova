"""Хелперы тестов обмена данными: книга в памяти, снимки истории и справочника.

Отдельный модуль, а не conftest: им пользуются и тесты импорта, и круговой
тест, а имя файла без префикса test_ pytest не подберёт как тест.
"""

import io

from django.utils import timezone
from openpyxl import Workbook

from workouts import excel
from workouts.models import Exercise, ExerciseSettings, Workout, cardio_parts_prefetch


def build_sheet(rows, *, header=None, sheet_title=excel.SHEET_TITLE, columns=excel.COLUMNS):
    """Книга из словарей по ключам колонок → поток, готовый для import_workbook.

    columns — формат книги: по умолчанию история, для справочника упражнений
    передаётся exercise_excel.COLUMNS. header — своя шапка (заголовками), когда
    нужно проверить недостающую или переставленную колонку.
    """
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_title
    titles = [column.title for column in columns] if header is None else header
    sheet.append(titles)
    keys = [_key_for(title, columns) for title in titles]
    for row in rows:
        sheet.append([row.get(key) for key in keys])
    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    return buffer


def _key_for(title, columns):
    for column in columns:
        if column.title == title:
            return column.key
    return title


def dump(user):
    """Снимок истории для кругового теста: то, что обязано пережить файл.

    done_at сюда не входит намеренно: метки времени подхода в таблице нет, как
    нет её и у тренировки, записанной задним числом.
    """
    snapshot = []
    workouts = (
        Workout.objects.filter(user=user)
        .finished()
        .select_related("sport", "location")
        .prefetch_related(cardio_parts_prefetch())
        .order_by("started_at", "id")
    )
    for workout in workouts:
        cardio = next(iter(workout.cardio_parts.all()), None)
        snapshot.append(
            {
                "started_at": timezone.localtime(workout.started_at).replace(
                    second=0, microsecond=0
                ),
                "sport": workout.sport.name,
                "location": workout.location.name if workout.location else "",
                "duration_min": workout.duration_min,
                "note": workout.note,
                "sets": [
                    (
                        row.exercise.name,
                        row.set_number,
                        row.measurement,
                        row.weight_kg,
                        row.reps,
                        row.duration_sec,
                        row.done,
                    )
                    for row in workout.sets.select_related("exercise").order_by("id")
                ],
                "notes": sorted(workout.exercise_notes.values_list("exercise__name", "text")),
                "cardio": (cardio.distance_km, cardio.avg_heart_rate) if cardio else None,
            }
        )
    return snapshot


def dump_exercises(user):
    """Снимок справочника пользователя: его упражнения, видимые общие и шаги веса.

    Для кругового теста и для изоляции: загрузка нетронутого файла не должна
    менять здесь ничего, а загрузка чужого — ничего у другого пользователя.
    """
    exercises = sorted(
        Exercise.objects.visible_to(user).values_list(
            "pk", "owner_id", "name", "muscle_group", "equipment", "measurement"
        )
    )
    steps = sorted(
        ExerciseSettings.objects.filter(user=user).values_list("exercise_id", "weight_step")
    )
    return {"exercises": exercises, "steps": steps}
