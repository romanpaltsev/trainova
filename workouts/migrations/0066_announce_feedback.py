# Новость «Что нового»: раздел «Обратная связь» — идеи, ошибки и вопросы автору.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Напишите нам: идеи, ошибки, вопросы"
BODY = (
    "Появилась «Обратная связь» — в профиле и в боковом меню на компьютере. "
    "Расскажите, чего не хватает, что работает не так или что непонятно: ответ "
    "придёт на почту и будет виден рядом с вашим сообщением."
)
PUBLISHED_ON = date(2026, 10, 9)


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(10, 0)))
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
        ("workouts", "0065_announce_exercise_create"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
