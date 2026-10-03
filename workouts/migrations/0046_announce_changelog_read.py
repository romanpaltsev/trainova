# Новость «Что нового» про метку «Новое», которая гаснет по нажатию.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.
# Сама эта запись после деплоя — непрочитанная у всех: на ней метку и попробуют.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "«Новое» гаснет, когда новость прочитана"
BODY = (
    "Метка «Новое» теперь стоит только у новостей, которые вы ещё не открывали. "
    "Нажмите на новость — метка погаснет, и сразу везде: прочитанное на телефоне "
    "считается прочитанным и на компьютере. Всё, что вышло до вашего прошлого "
    "визита в «Что нового», уже отмечено прочитанным. У исправлений по-прежнему "
    "серый ярлык «Исправлено»."
)
PUBLISHED_ON = date(2026, 10, 3)


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
        ("workouts", "0045_mark_seen_changelog_read"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
