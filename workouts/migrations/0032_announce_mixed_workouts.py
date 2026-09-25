# Новость «Что нового» про кардио внутри тренировки.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Штанга и дорожка — одна тренировка"
BODY = (
    "Раньше поход в зал, где после подходов была двадцатиминутная заминка, "
    "приходилось записывать двумя тренировками: дата дважды, место дважды, "
    "длительность пополам. И в ленте одно занятие выглядело как два. "
    "Теперь на экране тренировки есть кнопка «+ Кардио»: вид спорта, "
    "километры, время, пульс — и всё это внутри той же записи. "
    "Добавить можно когда угодно: разминка бегом до подходов и заминка на "
    "велосипеде после — обе встанут в том порядке, в котором вы их внесли. "
    "Время у кардио своё, и это важнее, чем кажется: темп теперь считается по "
    "нему, а не по длительности всего занятия. Пять километров за 25 минут "
    "посреди полуторачасовой тренировки — это 5:00 на километр, как и должно "
    "быть."
)
PUBLISHED_ON = date(2026, 9, 25)


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
        ("workouts", "0031_cardio_parts"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
