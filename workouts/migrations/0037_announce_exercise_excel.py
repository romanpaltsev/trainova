# Новость «Что нового» про справочник упражнений в Excel.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.
#
# Главное для человека — что можно поправить сразу много и что общие упражнения
# меняются только шагом веса: иначе правка названия общего упражнения, молча
# пропущенная загрузкой, выглядела бы поломкой. И про отмену: сохранённый файл.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Справочник упражнений — в Excel и обратно"
BODY = (
    "В «Экспорте и импорте» появился отдельный файл со справочником: все "
    "упражнения с группой мышц, снарядом, единицей и шагом веса. Удобно поправить "
    "сразу много — переименовать свои упражнения, проставить группы и снаряд, "
    "задать шаг веса гантелям — и загрузить таблицу обратно: изменения встанут во "
    "всю историю. У упражнений из общего справочника меняется только ваш шаг веса. "
    "Новые строки станут вашими упражнениями, а строки, удалённые из таблицы, "
    "ничего не удаляют. Сохраните скачанный файл до правок — его повторная "
    "загрузка вернёт всё как было."
)
PUBLISHED_ON = date(2026, 9, 26)


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
        ("workouts", "0036_announce_tidy_layout"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
