# Новость «Что нового» про дорогу к подготовленным тренировкам.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "План на неделю теперь на виду"
BODY = (
    "Подготовленные тренировки переехали на дашборд — блок «Подготовлено», "
    "ближайший день сверху. Раньше их приходилось искать в модалке «+», и план "
    "на неделю жил где-то за кадром. "
    "День спрашивается сразу: нажали «+» → «Подготовить» → выбрали число → вид "
    "спорта. Два тапа на каждую тренировку сэкономлены. "
    "И самое частое недоразумение: если в форме записи поставить дату из "
    "будущего, приложение больше не отвечает просто «нельзя». Оно предложит "
    "подготовить тренировку на этот день — со всем, что вы уже успели ввести."
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
        ("workouts", "0028_announce_backdated_workouts"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
