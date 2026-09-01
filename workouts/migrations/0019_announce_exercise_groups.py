# Новость «Что нового» про группы мышц в справочнике упражнений.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Упражнения по группам мышц"
BODY = (
    "Справочник упражнений больше не один длинный список. Теперь он разбит по "
    "группам мышц, а сверху появились кнопки: тап по «Спине» — и на экране только "
    "упражнения на спину. Строки стали компактнее, так что на экран телефона их "
    "влезает вдвое больше, а рекорд по-прежнему виден справа."
)
PUBLISHED_ON = date(2026, 9, 1)


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
        ("workouts", "0018_announce_exercise_order"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
