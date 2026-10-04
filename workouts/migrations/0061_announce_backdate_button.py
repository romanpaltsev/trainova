# Новость «Что нового»: «Записать задним числом» — не из карточки упражнения.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Дата прошедшей тренировки — у тренировки"
BODY = (
    "Кнопка «Записать задним числом» в подготовленной тренировке стояла внутри "
    "карточки упражнения, и казалось, что дату ставят упражнению. Теперь это "
    "«Записать тренировку задним числом» под карточкой: дата, время и длительность "
    "задаются всей тренировке."
)
PUBLISHED_ON = date(2026, 10, 4)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(22, 55)))
    entry_model.objects.get_or_create(
        title=TITLE,
        defaults={
            "kind": "fix",
            "body": BODY,
            "published_at": published_at,
            "is_published": True,
        },
    )


def remove_entry(apps, schema_editor):
    apps.get_model("workouts", "ChangelogEntry").objects.filter(title=TITLE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0060_rename_circuits_announcement"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
