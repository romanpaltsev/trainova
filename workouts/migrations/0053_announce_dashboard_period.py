# Новость «Что нового»: период сводки на главной.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.
# Время — по часу коммита (полночь на 4 октября), как у 0050: точка в профиле и в
# панели горит только у записей новее последнего открытия «Что нового».

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Сводка на главной — за любой период"
BODY = (
    "На главной теперь можно выбрать, за какой срок показывать сводку: 7 дней, "
    "30 дней, 3 месяца, год, всё время или свои даты — кнопка «Свой период». "
    "Цифры сравниваются с предыдущим таким же периодом. Открывается главная, как и "
    "раньше, со сводкой за 7 дней."
)
PUBLISHED_ON = date(2026, 10, 4)


def add_entry(apps, schema_editor):
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(0, 0)))
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
        ("workouts", "0052_announce_sidebar_news_dot"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
