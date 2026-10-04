# Новость «Что нового»: круги — суперсеты, трисеты и круг из любого числа упражнений.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Суперсеты, трисеты и круги"
BODY = (
    "Упражнения тренировки можно связать в круг — хоть два, хоть пять: нажмите "
    "«Связать в круг» под списком упражнений. Подходы пойдут по очереди — по одному "
    "каждого упражнения, а отдых начнётся только после круга. «+ Круг» добавляет "
    "ещё по подходу всем упражнениям круга, «Повторить» переносит круги в новую "
    "тренировку, а в Excel у них своя колонка «Круг»."
)
PUBLISHED_ON = date(2026, 10, 4)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(22, 15)))
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
        ("workouts", "0058_strengthset_circuit"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
