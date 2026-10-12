# Новость «Что нового»: набор высоты и подстановка маршрута в кардио.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Маршрут помнит километры"
BODY = (
    "Выберите в форме кардио привычное место — «Парк у реки», «Круг у дома» — и "
    "дистанция с набором высоты встанут сами, как в прошлый раз. Осталось вписать "
    "время: его мы за вас пока не пробежим. Набор высоты теперь записывается у "
    "любого кардио — будет чем объяснить, почему темп на горках не рекордный. На "
    "дорожке в зале всё по-прежнему: там километры — ваш результат, а не свойство "
    "места."
)
PUBLISHED_AT = (date(2026, 10, 12), time(15, 0))


def add_entry(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    published_at = timezone.make_aware(datetime.combine(*PUBLISHED_AT))
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
        ("workouts", "0087_cardiopart_elevation_gain_m"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
