# Новость «Что нового» про подготовку кардио-тренировок заранее.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Заезд и пробежку тоже можно готовить заранее"
BODY = (
    "Раньше подготовить заранее можно было только силовую. Теперь в окне «+» "
    "переключите «Подготовить» и выберите велосипед, бег или лыжи — спросим "
    "цель по дистанции, и план ляжет в «Подготовлено» рядом с силовыми. "
    "Когда съездите, откройте его оттуда: цель уже подставлена в дистанцию, "
    "останется вписать длительность и поправить километры по факту."
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
        ("workouts", "0024_announce_muscle_groups"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
