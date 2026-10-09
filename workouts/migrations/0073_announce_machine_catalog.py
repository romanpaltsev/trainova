# Новость «Что нового»: производители и модели тренажёров — списком, со своими записями.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Тренажёры — из списка, и свои тоже"
BODY = (
    "Производителя и модель тренажёра теперь выбирают из списка, а не набирают "
    "каждый раз. Нет нужного — добавьте своего производителя или модель прямо в "
    "окне выбора: они видны только вам. Переименовать и удалить свои записи можно "
    "в «Моих тренажёрах»."
)
PUBLISHED_ON = date(2026, 10, 9)


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(22, 0)))
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
        ("workouts", "0072_machine_catalog_fields"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
