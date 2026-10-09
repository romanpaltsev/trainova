# Новость «Что нового»: упражнение можно завести прямо в справочнике кнопкой
# «Создать», а не только в тренировке.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Своё упражнение — прямо из справочника"
BODY = (
    "В «Упражнениях» появилась кнопка «Создать»: задайте название, группу мышц, "
    "снаряд и как считается подход — и упражнение сразу откроется, чтобы настроить "
    "шаг веса. Раньше новое упражнение заводилось только посреди тренировки."
)
PUBLISHED_ON = date(2026, 10, 9)


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(9, 30)))
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
        ("workouts", "0064_announce_set_editing_fix"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
