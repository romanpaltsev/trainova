# Новость «Что нового» про карточку «Статистика» за год на дашборде компьютерной
# версии.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Статистика за год"
BODY = (
    "Внизу дашборда компьютерной версии появилась статистика по месяцам за год. "
    "Вкладки переключают время, тоннаж и дистанцию, у каждого вида спорта своя "
    "линия, а рядом — итог года. Удобно смотреть, как менялись тренировки от "
    "сезона к сезону."
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
        ("workouts", "0041_announce_weekly_goal"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
