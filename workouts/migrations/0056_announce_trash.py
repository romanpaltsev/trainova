# Новость «Что нового»: удалённую тренировку можно вернуть.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.
# Время — по часу коммита, как у 0050: точка горит только у записей новее
# последнего открытия «Что нового».

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Удалённую тренировку можно вернуть"
BODY = (
    "Если удалить тренировку по ошибке, её можно вернуть: кнопка «Восстановить» "
    "есть прямо в сообщении после удаления. А все удалённые за последние 30 дней "
    "лежат в разделе «Недавно удалённые» — он есть в профиле и внизу истории. "
    "Через 30 дней тренировки удаляются насовсем."
)
PUBLISHED_ON = date(2026, 10, 4)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(21, 45)))
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
        ("workouts", "0055_deletedworkout"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
