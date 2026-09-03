# Новость «Что нового» про цель по времени и день у подготовленной тренировки.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "План на неделю: день и цель по времени"
BODY = (
    "У подготовленной тренировки появился день — назначьте его, и «Подготовлено» "
    "выстроится по дням, ближайший сверху. У кардио день спрашивается в форме "
    "плана, у силовой ставится на её экране, рядом с местом. "
    "Цель теперь можно задать не только в километрах, но и во времени: «Бег · "
    "0:45» для тех, кто бегает на время. Обе цели необязательны — можно "
    "наготовить планов на неделю пустыми и заполнить по дороге. "
    "Когда тренировка состоится, откройте план: цели уже подставлены, останется "
    "поправить по факту."
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
        ("workouts", "0026_plan_targets"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
