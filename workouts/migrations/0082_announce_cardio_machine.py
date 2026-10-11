# Новость «Что нового»: тренажёр и его настройки у кардио.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Дорожка, уклон и уровень нагрузки — в кардио"
BODY = (
    "В кардио теперь можно записать, на каком тренажёре вы занимались, и его "
    "настройки: уклон дорожки и уровень нагрузки велотренажёра или эллипса. "
    "Раскройте «Тренажёр» в форме кардио или в окне «+ Кардио» на экране "
    "тренировки. В следующий раз в том же зале блок заполнится сам, как в "
    "прошлый раз, — останется поправить, если что-то поменяли."
)
PUBLISHED_AT = (date(2026, 10, 11), time(9, 0))


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
        ("workouts", "0081_cardio_part_machine"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
