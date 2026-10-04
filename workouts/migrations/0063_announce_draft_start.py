# Новость «Что нового»: «Начать тренировку» — вверху черновика, а не в карточке
# упражнения. Рядом с ней встала и «Записать тренировку задним числом», поэтому
# тем же файлом правится вышедшая час назад новость 0061: в ней сказано «под
# карточкой», а это больше неправда. Дата 0061 не меняется — точка «есть новое»
# горит из-за новой записи, а не из-за правки старой.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Кнопка «Начать тренировку» — вверху"
BODY = (
    "В подготовленной тренировке «Начать тренировку» стояла в карточке упражнения. "
    "Теперь она вверху, сразу под названием тренировки, — там, где после старта "
    "появится таймер отдыха. Рядом с ней — «Записать тренировку задним числом»."
)
PUBLISHED_ON = date(2026, 10, 4)

BACKDATE_TITLE = "Дата прошедшей тренировки — у тренировки"
BACKDATE_OLD_BODY = (
    "Кнопка «Записать задним числом» в подготовленной тренировке стояла внутри "
    "карточки упражнения, и казалось, что дату ставят упражнению. Теперь это "
    "«Записать тренировку задним числом» под карточкой: дата, время и длительность "
    "задаются всей тренировке."
)
BACKDATE_BODY = (
    "Кнопка «Записать задним числом» в подготовленной тренировке стояла внутри "
    "карточки упражнения, и казалось, что дату ставят упражнению. Теперь это "
    "«Записать тренировку задним числом»: дата, время и длительность задаются всей "
    "тренировке."
)


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(PUBLISHED_ON, time(23, 55)))
    entries.get_or_create(
        title=TITLE,
        defaults={
            "kind": "fix",
            "body": BODY,
            "published_at": published_at,
            "is_published": True,
        },
    )
    entries.filter(title=BACKDATE_TITLE, body=BACKDATE_OLD_BODY).update(body=BACKDATE_BODY)


def remove_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    entries.filter(title=TITLE).delete()
    entries.filter(title=BACKDATE_TITLE, body=BACKDATE_BODY).update(body=BACKDATE_OLD_BODY)


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0062_announce_easier_sets"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
