# Новость «Что нового» про подпись силовой тренировки группами мышц.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Видно, что за тренировка, не открывая её"
BODY = (
    "Силовые тренировки в истории и на дашборде больше не подписаны одинаковым "
    "словом «Силовая»: теперь в заголовке стоят группы мышц, которые в этот раз "
    "были — «Грудь · Плечи», «Ноги». Если групп больше двух, лишние сворачиваются "
    "в счётчик. Цветная точка осталась на месте, так что силовую от велосипеда "
    "по-прежнему видно с одного взгляда. Чтобы своё упражнение попадало в "
    "заголовок, задайте ему группу мышц на его странице — у упражнений из "
    "справочника она уже есть."
)
PUBLISHED_ON = date(2026, 9, 3)


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
        ("workouts", "0023_announce_locations"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
