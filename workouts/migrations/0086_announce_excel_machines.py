# Новость «Что нового»: тренажёры подходов и кардио внутри силовой — в Excel.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Тренажёры и кардио — в файле истории"
BODY = (
    "В файле истории из «Экспорта и импорта» колонки «Производитель» и «Модель» "
    "теперь заполнены и у силовых подходов — тренажёром, который указан у "
    "упражнения в этом зале. Кардио внутри силовой тренировки — разминка на "
    "дорожке, заминка на велосипеде — тоже попадает в файл, отдельной строкой со "
    "своим видом спорта, временем и тренажёром; раньше его там не было. Такой "
    "файл загружается обратно целиком."
)
PUBLISHED_AT = (date(2026, 10, 12), time(9, 0))


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(*PUBLISHED_AT))
    entries.get_or_create(
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
        ("workouts", "0085_announce_lean_catalog"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
