# Новость «Что нового»: ручной ввод значений, группа мышц и отзывчивость степпера.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Значения вписываются руками"
BODY = (
    "Вес, повторы и время теперь можно вписать: тап по числу открывает поле, и "
    "сорок нажатий «+» ради сотни килограммов больше не нужны. У своих упражнений "
    "появилась группа мышц — выбирается при создании и меняется на странице "
    "упражнения. И ещё: если связь в зале пропала, значение больше не застывает "
    "молча — приложение покажет, что не сохранило, и оживёт само."
)
PUBLISHED_ON = date(2026, 8, 31)


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
        ("workouts", "0013_announce_exercise_notes"),
    ]

    operations = [
        migrations.RunPython(add_entry, remove_entry),
    ]
