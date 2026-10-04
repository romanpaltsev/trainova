# Новость «Что нового»: подходы удобнее править — любой подход плана тапом, вес
# переходит на следующие, «+ Добавить подход» у выполненного упражнения.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Подходы удобнее править"
BODY = (
    "Любой подход в плане теперь открывается тапом, а не только текущий, — и в "
    "подготовленной тренировке тоже. Новый вес сам переходит на следующие подходы "
    "с тем же весом. А чтобы сделать ещё один подход уже выполненного упражнения, "
    "нажмите «+ Добавить подход» в его карточке."
)
PUBLISHED_ON = date(2026, 10, 4)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(23, 30)))
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
        ("workouts", "0061_announce_backdate_button"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
