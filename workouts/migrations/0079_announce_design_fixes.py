# Новость «Что нового»: смешанные тренировки в ленте, забытая тренировка, цвета
# своих видов спорта и свёрнутые настройки упражнения.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Понятнее в ленте и на странице упражнения"
BODY = (
    "Тренировка, где были и подходы, и кардио, теперь так и подписана — "
    "«Пресс + Бег», — а в карточке видны и тоннаж, и километры. Время удержания "
    "в строке подписано, и «0:49 · 1:45» больше не читается как две длительности. "
    "Если тренировку забыли завершить, экран тренировки об этом скажет, а при "
    "завершении спросит, сколько она длилась на самом деле, — сутки в историю "
    "не попадут. У своих видов спорта теперь свой цвет, а не цвет бега. На "
    "телефоне настройки упражнения свёрнуты в одну строку, и история подходов "
    "видна сразу."
)
PUBLISHED_AT = (date(2026, 10, 11), time(1, 0))


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(*PUBLISHED_AT))
    entries.get_or_create(
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
        ("workouts", "0078_sport_palette"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
