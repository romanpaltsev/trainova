# Новость «Что нового» про ревизию отступов и раскладки.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.
#
# Пишем о том, что человек заметит сам: карточки без отступов на «Экспорте и
# импорте», россыпь настроек на странице упражнения, разнобой «+ Кардио» и
# «+ Упражнение», дыры в колонках на компьютере. Токены и сетки — не для новостей.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Экраны стали аккуратнее"
BODY = (
    "Навели порядок в отступах. На странице «Экспорт и импорт» текст больше не "
    "прилипает к краям карточек, настройки упражнения собраны в одну компактную "
    "карточку, а кнопка «+ Кардио» выглядит так же, как «+ Упражнение». На "
    "компьютере дашборд и экран тренировки больше не оставляют пустых дыр между "
    "колонками."
)
PUBLISHED_ON = date(2026, 9, 26)


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
        ("workouts", "0035_announce_precise_exercise_names"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
