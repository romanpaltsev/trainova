# Новость «Что нового» про справочник мест тренировок.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Тренировка помнит, где она была"
BODY = (
    "Появились места: зал, дом, маршрут — всё в одном справочнике. Зал, куда "
    "ходите постоянно, отметьте звездой в профиле один раз — дальше он сам "
    "подставится в каждую силовую тренировку, а на экране тренировки его можно "
    "сменить. В форме кардио место спрашивается: впишите, куда поехали, и оно "
    "запомнится. Место видно в истории, и по нему фильтруется лента. "
    "Опечатку не переписывать заново — переименуйте место в «Моих местах», и "
    "оно исправится во всех тренировках сразу."
)
PUBLISHED_ON = date(2026, 9, 2)


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
        ("workouts", "0021_locations"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
