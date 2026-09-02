# Новость «Что нового» про блок «Я тренирую» в справочнике упражнений.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Свои упражнения — сразу сверху"
BODY = (
    "В справочнике было две с половиной дюжины упражнений, и те шесть, которые вы "
    "правда делаете, ничем не выделялись. Теперь сверху блок «Я тренирую»: у "
    "каждого упражнения рекорд, сколько было тренировок и когда последняя — "
    "свежие идут первыми. Ниже остался полный справочник по группам мышц, так что "
    "в «Груди» по-прежнему всё про грудь. Когда ищете или включаете фильтр, "
    "разделение убирается и остаётся один список результатов."
)
PUBLISHED_ON = date(2026, 9, 2)


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
        ("workouts", "0020_announce_live_recovery"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
