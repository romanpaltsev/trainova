# Новость «Что нового» про компьютерную версию во всю ширину.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.
#
# Главное для человека — что на компьютере экраны больше не узкая колонка, и где
# это заметнее всего: тренировка, итог, профиль, окна. И что на телефоне ничего
# не поменялось — иначе новость о «переделке» читалась бы как повод искать, что
# сломалось в привычном экране.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "На компьютере — во всю ширину экрана"
BODY = (
    "На компьютере дневник больше не жмётся узкой колонкой посреди экрана. "
    "Тренировка раскладывается на три колонки: текущее упражнение, очередь и "
    "выполненное вместе с таймером отдыха. В итоге упражнения идут колонками, а "
    "«Повторить» — сразу в шапке. Профиль собран плитками, история, справочник и "
    "«Экспорт и импорт» используют всю ширину, форма кардио — в две колонки. Окна "
    "выбора упражнения и новой тренировки стали шире: найденное и создание нового "
    "стоят рядом. На телефоне всё как было."
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
        ("workouts", "0037_announce_exercise_excel"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
