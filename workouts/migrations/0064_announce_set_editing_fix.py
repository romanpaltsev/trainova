# Новость «Что нового»: вес больше не переходит на следующие подходы, а новый
# подход сразу открыт для правки. Перенос веса обещала вышедшая новость 0062 —
# её фраза убирается тем же файлом, приём 0063. Дата 0062 не меняется.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Каждый подход — сам за себя"
BODY = (
    "Поправили вес одного подхода — а соседи тут же подтянулись следом. Задумывалось "
    "как помощь, вышло как самоуправство: теперь меняется ровно тот подход, который "
    "вы правите. А «+ Добавить подход» сразу открывает новый подход, а не первый в "
    "плане, — крутить степперы можно без лишнего тапа."
)
PUBLISHED_ON = date(2026, 10, 8)

EASIER_SETS_TITLE = "Подходы удобнее править"
EASIER_SETS_OLD_BODY = (
    "Любой подход в плане теперь открывается тапом, а не только текущий, — и в "
    "подготовленной тренировке тоже. Новый вес сам переходит на следующие подходы "
    "с тем же весом. А чтобы сделать ещё один подход уже выполненного упражнения, "
    "нажмите «+ Добавить подход» в его карточке."
)
EASIER_SETS_BODY = (
    "Любой подход в плане теперь открывается тапом, а не только текущий, — и в "
    "подготовленной тренировке тоже. А чтобы сделать ещё один подход уже "
    "выполненного упражнения, нажмите «+ Добавить подход» в его карточке."
)


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(12, 0)))
    entries.get_or_create(
        title=TITLE,
        defaults={
            "kind": "fix",
            "body": BODY,
            "published_at": published_at,
            "is_published": True,
        },
    )
    entries.filter(title=EASIER_SETS_TITLE, body=EASIER_SETS_OLD_BODY).update(
        body=EASIER_SETS_BODY
    )


def remove_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    entries.filter(title=TITLE).delete()
    entries.filter(title=EASIER_SETS_TITLE, body=EASIER_SETS_BODY).update(
        body=EASIER_SETS_OLD_BODY
    )


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0063_announce_draft_start"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
