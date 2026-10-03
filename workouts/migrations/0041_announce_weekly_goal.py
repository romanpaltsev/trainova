# Новость «Что нового» про цель на неделю на дашборде компьютерной версии.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.
#
# Человеку важно: что это, как считается (все тренировки, неделя с понедельника)
# и где её задать.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Цель на неделю"
BODY = (
    "На дашборде компьютерной версии появилась цель на неделю: задайте, сколько "
    "часов хотите тренироваться, и шкала покажет, какая часть уже сделана, сколько "
    "осталось и сколько было на прошлой неделе. Считаются все тренировки — силовые, "
    "кардио и смешанные — с понедельника по воскресенье. Задать или поменять цель "
    "можно прямо в карточке."
)
PUBLISHED_ON = date(2026, 10, 3)


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
        ("workouts", "0040_announce_dashboard_tiles"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
