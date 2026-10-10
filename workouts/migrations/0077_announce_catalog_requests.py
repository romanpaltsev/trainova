# Новость «Что нового»: заявки в общий справочник упражнений и тренажёров.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Пополните общий справочник"
BODY = (
    "Завели своё упражнение, производителя или модель тренажёра, которых нет в "
    "общем списке? Предложите их всем: на странице своего упражнения — «Предложить "
    "в общий справочник», на «Моих тренажёрах» — значок со стрелкой. Если заявку "
    "примут, запись станет общей, а ваши тренировки с ней останутся как есть. "
    "Решение придёт на почту, а все заявки видны в профиле — «Мои заявки»."
)
PUBLISHED_ON = date(2026, 10, 10)


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(22, 0)))
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
        ("workouts", "0076_catalog_request"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
