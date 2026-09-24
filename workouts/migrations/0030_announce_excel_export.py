# Новость «Что нового» про обмен историей через Excel.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Дневник — в Excel и обратно"
BODY = (
    "Всю историю теперь можно скачать одним файлом: профиль → «Экспорт и импорт» "
    "→ «Скачать .xlsx». Один лист, строка — подход: дата, вид спорта, место, "
    "упражнение, вес, повторы, удержание; у кардио — дистанция и пульс. "
    "Такую таблицу удобно отправить тренеру или просто посмотреть свои цифры "
    "по-своему. "
    "Тот же файл загружается обратно — так в дневник переезжают тренировки из "
    "таблицы, которую вы вели раньше. Упражнения, места и виды спорта, которых "
    "в приложении ещё нет, оно заведёт само и перечислит после загрузки. "
    "Тренировки, которые в дневнике уже есть — тот же день и вид спорта, — "
    "не задвоятся: исправленный файл можно загружать повторно без опаски."
)
PUBLISHED_ON = date(2026, 9, 25)


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
        ("workouts", "0029_announce_planned_on_dashboard"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
