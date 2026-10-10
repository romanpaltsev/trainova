# Новость «Что нового»: производитель и модель — у гантелей, штанги и гирь тоже.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Производитель — и у гантелей со штангой"
BODY = (
    "Гантели и блины в разных залах разные, поэтому производителя и модель теперь "
    "можно указать у любого упражнения, а не только на тренажёре: у гантелей, "
    "штанги, гири. Во время тренировки под упражнением появилась кнопка "
    "«Производитель», на странице упражнения — секция с тем же названием. У "
    "упражнений с собственным весом кнопки нет."
)
PUBLISHED_AT = (date(2026, 10, 11), time(1, 30))


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
        ("workouts", "0079_announce_design_fixes"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
