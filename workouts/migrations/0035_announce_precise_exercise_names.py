# Новость «Что нового» про точный справочник упражнений.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.
#
# Текст обязан объяснить человеку с личным дублем, почему в списке две похожие
# строки: у кого «Жим гантелей лежа в наклоне» заведён своим, тот увидит рядом
# и новую запись справочника. Свои записи не тронуты намеренно — это его данные.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Справочник упражнений стал точнее"
BODY = (
    "«Жим лёжа» не говорил, штанга это или гантели, а «Жим лёжа в наклоне» — "
    "какой наклон. В зале это разные упражнения с разными весами, и в одном "
    "списке они путались. Теперь в справочнике 64 упражнения с точными "
    "названиями: «Жим штанги лёжа», «Жим гантелей на наклонной скамье», "
    "«Тяга нижнего блока к поясу». У каждого указан снаряд — штанга, гантели, "
    "тренажёр, блок, гиря или собственный вес, — и по нему в каталоге есть "
    "второй ряд фильтров: тап по «Гантелям» оставляет в списке только их. "
    "Старые упражнения не заведены заново, а переименованы: вся история, "
    "рекорды и графики остались на месте. Если вы уже заводили своё "
    "упражнение с похожим названием, оно тоже никуда не делось — и теперь его "
    "можно переименовать: нажмите на название на странице упражнения. "
    "Новое имя встанет сразу во всю историю."
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
        ("workouts", "0034_precise_exercise_names"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
