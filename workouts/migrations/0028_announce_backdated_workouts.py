# Новость «Что нового» про запись тренировки задним числом.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Тетрадка переезжает в приложение"
BODY = (
    "Тренировки, записанные когда-то на бумаге, теперь можно внести как есть. "
    "Подготовьте тренировку как обычно — упражнения, подходы, веса, — а вместо "
    "«Начать» нажмите «Записать задним числом»: спросим дату, время и сколько "
    "всё это длилось. Время можно не указывать, поставим полдень. "
    "Упражнения встанут в том порядке, в котором вы их набрали. "
    "Заодно у записанной силовой появилась правка даты и длительности — на её "
    "итоге, рядом с местом. Промахнулись днём, переписывая тетрадку, — "
    "поправьте одним тапом. У кардио теперь спрашивается и время начала: "
    "вечерняя пробежка больше не превращается в полуденную."
)
PUBLISHED_ON = date(2026, 9, 24)


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
        ("workouts", "0027_announce_plan_targets"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
