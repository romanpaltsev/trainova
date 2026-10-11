# Новость «Что нового»: тренажёр и уклон кардио — в Excel.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Тренажёр и уклон — в выгрузке Excel"
BODY = (
    "В файле истории из «Экспорта и импорта» у кардио-тренировок появились "
    "колонки «Производитель», «Модель», «Уклон, %» и «Уровень». Их можно и "
    "заполнять: при загрузке незнакомый тренажёр заведётся сам, а файлы, "
    "скачанные раньше, загружаются как прежде."
)
PUBLISHED_AT = (date(2026, 10, 11), time(9, 30))


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(*PUBLISHED_AT))
    entries.get_or_create(
        title=TITLE,
        defaults={
            "kind": "feature",
            "body": BODY,
            "published_at": published_at,
            "is_published": True,
        },
    )


def remove_entry(apps, schema_editor):
    apps.get_model("workouts", "ChangelogEntry").objects.filter(title=TITLE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0082_announce_cardio_machine"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
