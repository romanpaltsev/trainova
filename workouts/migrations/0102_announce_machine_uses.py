# Новость «Что нового»: тап по тренажёру на «Моих тренажёрах» показывает, где он стоит.
#
# Анонс едет вместе с правкой одноразовой миграцией — см. 0008_announce_planned_workouts.

from datetime import date, datetime, time

from django.db import migrations
from django.utils import timezone

TITLE = "Железо сдаёт явки"
BODY = (
    "В «Моих тренажёрах» тапни по производителю или модели — вылезет список упражнений, "
    "где у тебя прописано это железо: с залом, а у производителя ещё и с моделью, и оттуда "
    "сразу на страницу упражнения. Дорожки и велики там же покажут кардио — сколько раз "
    "гонял и когда в последний. Переименовать — тапом по названию в этом окне. Больше "
    "никаких «а на чём я жал ногами в том зале?»."
)
PUBLISHED_AT = (date(2026, 10, 18), time(23, 30))


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
        ("workouts", "0101_announce_muscle_groups"),
    ]

    operations = [migrations.RunPython(add_entry, remove_entry)]
