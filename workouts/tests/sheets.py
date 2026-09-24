"""Хелперы тестов обмена данными: книга в памяти и снимок истории.

Отдельный модуль, а не conftest: им пользуются и тесты импорта, и круговой
тест, а имя файла без префикса test_ pytest не подберёт как тест.
"""

import io

from django.utils import timezone
from openpyxl import Workbook

from workouts import excel
from workouts.models import Workout

KEYS = [column.key for column in excel.COLUMNS]
TITLES = [column.title for column in excel.COLUMNS]


def build_sheet(rows, *, header=None, sheet_title=excel.SHEET_TITLE):
    """Книга из словарей по ключам COLUMNS → поток, готовый для import_workbook."""
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_title
    titles = TITLES if header is None else header
    sheet.append(titles)
    keys = KEYS if header is None else [_key_for(title) for title in header]
    for row in rows:
        sheet.append([row.get(key) for key in keys])
    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    return buffer


def _key_for(title):
    for column in excel.COLUMNS:
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
        .select_related("sport", "location", "cardio")
        .order_by("started_at", "id")
    )
    for workout in workouts:
        cardio = getattr(workout, "cardio", None)
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
