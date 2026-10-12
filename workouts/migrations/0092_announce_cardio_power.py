# Новость «Что нового»: средняя мощность у кардио.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Ватты с табло"
BODY = (
    "Рядом с пульсом в кардио появилась средняя мощность — переписывайте её с "
    "табло велотренажёра, гребного, лыжного тренажёра или эллипса, а велосипедисты "
    "с измерителем — прямо с часов. У тренажёра без километров в истории теперь "
    "видны ватты, а не скромный прочерк. Из прошлого раза мощность не "
    "подставляется: это ваш результат, и сегодня он может быть больше."
)
PUBLISHED_AT = (date(2026, 10, 12), time(15, 40))


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
        ("workouts", "0091_cardiopart_avg_power_w"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
