# Новость «Что нового»: свой аккаунт можно удалить — с отсрочкой 30 дней.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Аккаунт можно удалить"
BODY = (
    "В профиле, в разделе «Аккаунт», появилась кнопка «Удалить аккаунт». Перед "
    "удалением можно скачать историю и справочник в Excel. Аккаунт сразу "
    "перестаёт работать, но 30 дней его можно вернуть — просто войдите. Потом "
    "все данные удаляются насовсем."
)
PUBLISHED_ON = date(2026, 10, 4)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(21, 55)))
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
        ("workouts", "0056_announce_trash"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
