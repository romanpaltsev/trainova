# Новость «Что нового» про плитки с динамикой и таблицу последних тренировок
# на дашборде компьютерной версии.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.
#
# Человеку важно, что теперь видно без подсчётов в уме: стало больше или меньше,
# чем на прошлой неделе, сколько дней из семи он тренировался и где.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Дашборд на компьютере: динамика и таблица"
BODY = (
    "На компьютере плитки дашборда показывают, как изменились тренировки, время, "
    "тоннаж и дистанция по сравнению с прошлыми семью днями: рост — зелёным. Под "
    "числами — сколько дней из семи вы тренировались и средняя длительность "
    "тренировки. Последние тренировки собраны в таблицу: дата, время, нагрузка, "
    "место и тип — силовая, кардио или смешанная."
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
        ("workouts", "0039_announce_sidebar"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
