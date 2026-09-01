# Новость «Что нового» про порядок упражнений и номера.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Видно, в каком порядке делали упражнения"
BODY = (
    "У упражнений в тренировке появился номер — теперь понятно, что было первым, "
    "а что четвёртым. Причём номер по факту, а не по плану: если вы подготовили "
    "тренировку заранее, а в зале начали с другого упражнения, запись это "
    "запомнит. В живом режиме нумерация сквозная — сразу видно, сколько уже "
    "сделано и что осталось. У тренировок, записанных раньше, номера идут в том "
    "порядке, в котором упражнения добавлялись: времени выполнения в старых "
    "записях просто нет."
)
PUBLISHED_ON = date(2026, 9, 1)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(12, 0)))
    entry_model.objects.get_or_create(
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
        ("workouts", "0017_set_done_at"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
