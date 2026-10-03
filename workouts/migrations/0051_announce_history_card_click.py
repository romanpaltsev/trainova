# Новость «Что нового»: силовая тренировка в истории открывается нажатием на карточку.
#
# Анонс едет вместе с доработкой одноразовой миграцией — см. 0008_announce_planned_workouts.
# Время — по часу коммита, как у 0050: точка в профиле горит только у записей новее
# последнего открытия «Что нового».

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Силовая тренировка в истории открывается нажатием на карточку"
BODY = (
    "Раньше силовую тренировку в истории открывала только кнопка «Открыть». Теперь "
    "достаточно нажать на карточку — так же, как у кардио. Кнопка «Повторить» "
    "осталась на месте."
)
PUBLISHED_ON = date(2026, 10, 3)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(23, 0)))
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
        ("workouts", "0050_announce_body_measurements"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
