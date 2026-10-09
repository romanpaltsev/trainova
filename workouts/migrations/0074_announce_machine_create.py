# Новость «Что нового»: производителей и модели можно завести на «Моих тренажёрах».
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Тренажёры можно добавить заранее"
BODY = (
    "В «Моих тренажёрах» появилась кнопка «Создать»: производителя или модель "
    "теперь можно добавить заранее, не открывая упражнение. А «+ Модель» под "
    "производителем добавит модель сразу к нему."
)
PUBLISHED_ON = date(2026, 10, 9)


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(22, 30)))
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
        ("workouts", "0073_announce_machine_catalog"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
