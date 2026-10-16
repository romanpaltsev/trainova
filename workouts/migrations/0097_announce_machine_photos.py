# Новость «Что нового»: фото у моделей тренажёров.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Тренажёр теперь узнать в лицо"
BODY = (
    "У моделей тренажёров появились фото. Нажмите на фотоаппарат в окне «Тренажёр» "
    "прямо у снаряда или на «Моих тренажёрах» — и в следующий раз тот самый жим ногами "
    "найдётся по картинке, а не по загадочному «Selection 900». Снимок сам уменьшится "
    "и забудет, где его сделали, а фото своей модели видите только вы — пока не "
    "предложите её в общий список."
)
PUBLISHED_AT = (date(2026, 10, 16), time(13, 0))


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
        ("workouts", "0096_machine_model_photo"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
