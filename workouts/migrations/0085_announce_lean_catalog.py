# Новость «Что нового»: справочник упражнений очищен от неиспользованных.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Справочник упражнений стал короче"
BODY = (
    "В справочнике остались только упражнения, которые кто-то уже делал: "
    "длинный список «на всякий случай» мешал найти нужное. Ваша история не "
    "изменилась. Нет нужного упражнения — нажмите «Создать» в «Упражнениях» или "
    "добавьте его прямо во время тренировки, а удачное своё можно предложить в "
    "общий справочник."
)
PUBLISHED_AT = (date(2026, 10, 11), time(10, 0))


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
        ("workouts", "0084_delete_unused_exercises"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
