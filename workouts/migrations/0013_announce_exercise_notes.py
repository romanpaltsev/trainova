# Новость «Что нового» про заметки к упражнениям.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Заметки к упражнениям"
BODY = (
    "Теперь к упражнению в тренировке можно прикрепить заметку — «болело плечо», "
    "«взял узкий хват». В следующий раз, когда дойдёте до этого упражнения, она "
    "покажется рядом с прошлыми весами. Писать можно и по ходу тренировки, и заранее "
    "в подготовленной; в записанной тренировке заметка остаётся, но только для чтения."
)
PUBLISHED_ON = date(2026, 8, 31)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(11, 0)))
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
        ("workouts", "0012_exercise_note"),
    ]

    operations = [
        migrations.RunPython(add_entry, remove_entry),
    ]
