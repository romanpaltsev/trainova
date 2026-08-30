# Новость «Что нового» про единицы измерения упражнений.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Упражнения без килограммов"
BODY = (
    "Не все упражнения меряются килограммами. Планка теперь считается временем: "
    "в подходе появились кнопки ±15 секунд, а в личных рекордах — лучшее удержание. "
    "У своих упражнений единицу можно выбрать при создании и поменять на странице "
    "упражнения: вес с повторами, только повторы, время или время с весом."
)
PUBLISHED_ON = date(2026, 8, 31)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(10, 0)))
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
        ("workouts", "0010_planka_is_measured_in_time"),
    ]

    operations = [
        migrations.RunPython(add_entry, remove_entry),
    ]
