# Новость «Что нового» про справочник упражнений таблицей на компьютере.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.
#
# Человеку важно: где теперь видно всё про упражнение сразу, как отсортировать
# и что упражнение открывается справа, не уходя из таблицы. И что на телефоне
# ничего не поменялось.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Справочник упражнений — таблицей"
BODY = (
    "На компьютере справочник упражнений стал таблицей: группа мышц, сколько раз "
    "вы делали упражнение, когда в последний раз и рекорд — всё в одной строке. "
    "Нажмите на заголовок колонки, чтобы отсортировать, — например, по числу "
    "тренировок; повторное нажатие разворачивает порядок. Упражнение открывается "
    "справа поверх таблицы: закрыть — крестиком или клавишей Esc. На телефоне всё "
    "как было."
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
        ("workouts", "0042_announce_dashboard_stats"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
