# Новость «Что нового» про боковое меню на компьютере.
#
# Анонс едет вместе с фичей одноразовой миграцией — см. 0008_announce_planned_workouts.
#
# Человеку важно, куда делись разделы из шапки (они слева), что меню можно
# свернуть и что появился поиск с клавишей «/». И что на телефоне ничего не
# поменялось.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "На компьютере — меню слева"
BODY = (
    "На компьютере разделы переехали в меню слева: дашборд, история и "
    "упражнения, а под ними профиль, места, виды спорта, экспорт и «Что нового» — "
    "всё в один клик, без захода в профиль. Меню сворачивается до значков кнопкой "
    "в шапке и запоминает выбор. В шапке появился поиск по упражнениям: нажмите «/», "
    "и можно сразу печатать. На телефоне всё как было."
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
        ("workouts", "0038_announce_desktop_layout"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
