# Новость «Что нового» про свой шаг веса у упражнения.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Свой шаг веса у каждого упражнения"
BODY = (
    "Кнопки «−» и «+» больше не привязаны к 2,5 кг на всё подряд. На странице "
    "упражнения появился «Шаг веса»: у приседа со штангой оставьте 2,5 кг, для "
    "гантелей поставьте 0,5, а на микроблинах — 0,25. Шаг можно задать и у "
    "упражнений из общего справочника, и он только ваш: у остальных остаётся свой."
)
PUBLISHED_ON = date(2026, 8, 31)


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
        ("workouts", "0015_exercisesettings"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
