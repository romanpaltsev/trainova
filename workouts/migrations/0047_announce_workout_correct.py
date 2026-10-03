# Новость «Что нового» про правку уже записанной силовой тренировки.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Прошедшую тренировку можно исправить"
BODY = (
    "Забыли записать упражнение или ошиблись весом — откройте тренировку в "
    "истории и нажмите «Изменить тренировку». Нажмите на подход, чтобы поправить "
    "вес, повторы или время, добавьте подход или упражнение, допишите заметку. "
    "Тоннаж и рекорды пересчитаются сами."
)
PUBLISHED_ON = date(2026, 10, 3)


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
        ("workouts", "0046_announce_changelog_read"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
