# Новость «Что нового»: тренажёр упражнения — производитель и модель для каждого зала.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Тренажёр: производитель и модель в каждом зале"
BODY = (
    "Отметьте, на каком тренажёре делаете упражнение: производитель и модель — "
    "отдельно для каждого зала. Указать можно на странице упражнения или прямо во "
    "время тренировки, и в следующий раз будет видно, какую машину искать."
)
PUBLISHED_ON = date(2026, 10, 9)


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(21, 0)))
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
        ("workouts", "0068_exercisemachine"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
