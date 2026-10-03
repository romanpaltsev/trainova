# Новость «Что нового» про раздел «Мои замеры».
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.
# Время не полуденное, как у соседних анонсов этого дня: точка в профиле горит
# только у записей новее последнего открытия «Что нового», а его сегодня уже
# открывали после полудня.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Мои замеры: вес, рост и обхваты"
BODY = (
    "В профиле появился раздел «Мои замеры»: вес, рост, процент жира, обхваты и "
    "пульс в покое. Каждый замер записывается с датой — рядом видно, насколько "
    "значение изменилось с прошлого раза, а со второго замера появляется график. "
    "Не хватает параметра — добавьте свой, например «Шея» в сантиметрах."
)
PUBLISHED_ON = date(2026, 10, 3)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(20, 30)))
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
        ("workouts", "0049_global_body_metrics"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
