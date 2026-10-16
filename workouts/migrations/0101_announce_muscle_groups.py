# Новость «Что нового»: «Руки» и «Ноги» разнесли по мышцам.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Руки — это не группа мышц"
BODY = (
    "Разнесли «Руки» и «Ноги» по мышцам: теперь есть бицепс, трицепс, предплечья, "
    "квадрицепс, бицепс бедра, ягодицы и икры. Общие упражнения мы уже разложили по "
    "полочкам, а своим поставь группу чипом на странице упражнения — новые группы "
    "там всегда под рукой. В истории сразу видно, был день бицухи или трицепса, а не "
    "загадочные «Руки»."
)
PUBLISHED_AT = (date(2026, 10, 16), time(14, 20))


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
        ("workouts", "0100_split_arm_and_leg_groups"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
