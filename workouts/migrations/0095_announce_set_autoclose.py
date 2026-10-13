# Новость «Что нового»: открытый подход сворачивается сам, когда уходят из
# карточки упражнения.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "«Готово» понимает намёки"
BODY = (
    "Открыли подход, подкрутили вес — и можно не тянуться к «Готово»: тапните мимо "
    "карточки упражнения, и подход свернётся сам, а новое значение сохранится. В "
    "живом режиме и в черновике панель так же вернётся к текущему подходу, поближе "
    "к «Подход выполнен». Сама кнопка никуда не делась — просто теперь её можно "
    "не нажимать."
)
PUBLISHED_AT = (date(2026, 10, 13), time(19, 0))


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
        ("workouts", "0094_announce_gear"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
