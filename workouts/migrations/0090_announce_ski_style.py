# Новость «Что нового»: стиль хода у лыж — конёк или классика.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Конёк или классика"
BODY = (
    "У лыж теперь есть стиль: под видом спорта появились чипы «Конёк» и "
    "«Классика», а выбранный в прошлый раз подставляется сам. В истории лыжня "
    "подписана как «Лыжи · конёк», а рекорды больше не смешивают коньковый темп "
    "с классическим — классика наконец перестанет выглядеть медленной. "
    "Лыжероллерам, заведённым своим видом спорта, стили достались тоже."
)
PUBLISHED_AT = (date(2026, 10, 12), time(15, 30))


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
        ("workouts", "0089_cardiopart_style"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
