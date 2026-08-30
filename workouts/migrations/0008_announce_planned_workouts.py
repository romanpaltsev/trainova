# Новость «Что нового» про подготовку тренировки заранее.
#
# Анонс едет вместе с фичей одноразовой миграцией, а не через seed: seed
# наполняет только стартовые новости пустой базы и при повторном запуске
# пересоздал бы запись, переименованную в админке. Миграция применяется один
# раз, а дальше запись — обычная строка, её можно править и снимать с
# публикации в админке.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Подготовка тренировки заранее"
BODY = (
    "Тренировку теперь можно собрать заранее: нажмите «+», переключитесь на "
    "«Подготовить» и добавьте упражнения — время при этом не идёт. В зале "
    "откройте её из того же «+» и нажмите «Начать тренировку»: отсчёт начнётся "
    "с этого момента. Подготовить можно сразу несколько — хоть на всю неделю."
)
# Дата зашита, а не timezone.now(): на свежей базе миграции применяются разом,
# и «сейчас» слепило бы все прошлые анонсы в один день.
PUBLISHED_ON = date(2026, 8, 30)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(10, 0)))
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
        ("workouts", "0007_planned_workouts"),
    ]

    operations = [
        migrations.RunPython(add_entry, remove_entry),
    ]
