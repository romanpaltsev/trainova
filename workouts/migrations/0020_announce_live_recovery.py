# Новость «Что нового» про восстановление живого экрана после паузы.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Живой экран не залипает после паузы"
BODY = (
    "Если телефон долго лежал в кармане, живой экран мог перестать отвечать на "
    "тапы — помогал только переход на другую страницу и обратно. Теперь при "
    "возвращении к экрану после паузы он обновляется сам. А если запрос не "
    "дошёл до сервера, сверху появляется полоса «Нет связи — изменение не "
    "сохранено» с кнопкой «Обновить»: раньше такой сбой проходил молча."
)
PUBLISHED_ON = date(2026, 9, 2)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(12, 0)))
    entry_model.objects.get_or_create(
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
        ("workouts", "0019_announce_exercise_groups"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
