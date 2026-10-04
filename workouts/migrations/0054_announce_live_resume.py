# Новость «Что нового»: экран тренировки отвечает сразу после перерыва.
#
# Анонс едет вместе с исправлением одноразовой миграцией — см. 0008_announce_planned_workouts.
# Время — по часу коммита, как у 0050: точка горит только у записей новее
# последнего открытия «Что нового».

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Экран тренировки отвечает сразу после перерыва"
BODY = (
    "Раньше, если отойти от телефона больше чем на минуту, экран тренировки при "
    "возвращении сам перезагружался, и несколько секунд нажатия не срабатывали — "
    "казалось, что пропала связь. Теперь он перезагружается, только если правда "
    "завис, а после отдыха между подходами всё нажимается сразу."
)
PUBLISHED_ON = date(2026, 10, 4)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(18, 10)))
    entry_model.objects.get_or_create(
        title=TITLE,
        defaults={
            "kind": "fix",
            "body": BODY,
            "published_at": published_at,
            "is_published": True,
        },
    )


def remove_entry(apps, schema_editor):
    apps.get_model("workouts", "ChangelogEntry").objects.filter(title=TITLE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0053_announce_dashboard_period"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
