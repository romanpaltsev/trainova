# Новость «Что нового»: своё снаряжение с пробегом.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Кроссовки считают километры"
BODY = (
    "В кардио появилось «Снаряжение»: впишите кроссовки, велосипед или лыжи прямо в "
    "форме — в следующий раз они подставятся сами, а к коньку — коньковые лыжи. "
    "Пробег копится из дистанций, смотреть его — в «Моём снаряжении» профиля. Там "
    "же кнопка «на пенсию»: пробег сохранится, а о замене кроссовок вы узнаете "
    "раньше колен."
)
PUBLISHED_AT = (date(2026, 10, 12), time(15, 55))


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
        ("workouts", "0093_gear"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
