# Новость «Что нового»: переписка в обратной связи и точка «есть новый ответ».
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Обратная связь: теперь можно переписываться"
BODY = (
    "Теперь на ответ в «Обратной связи» можно ответить: откройте своё сообщение "
    "и допишите уточнение. А когда приходит новый ответ, у «Обратной связи» в "
    "профиле и в боковом меню загорается точка."
)
PUBLISHED_ON = date(2026, 10, 9)


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(12, 0)))
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
        ("workouts", "0066_announce_feedback"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
