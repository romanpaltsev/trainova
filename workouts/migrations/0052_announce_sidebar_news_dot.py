# Новость «Что нового»: точка «есть новое» в боковой панели на компьютере.
#
# Анонс едет вместе с доработкой одноразовой миграцией — см. 0008_announce_planned_workouts.
# Время — по часу коммита, как у 0050: точка горит только у записей новее
# последнего открытия «Что нового».

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Точка у «Что нового» в панели на компьютере"
BODY = (
    "На компьютере у пункта «Что нового» в боковой панели теперь загорается точка, "
    "когда вышли новости, которые вы ещё не видели, — как в профиле на телефоне. "
    "Откройте «Что нового», и точка погаснет."
)
PUBLISHED_ON = date(2026, 10, 3)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(23, 40)))
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
        ("workouts", "0051_announce_history_card_click"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
