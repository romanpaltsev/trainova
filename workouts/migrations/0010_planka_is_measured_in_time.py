# Глобальная «Планка» считается временем, а не килограммами.
#
# Меняем единицу только у неё. Подтягивания, отжимания на брусьях, скручивания и
# гиперэкстензию оставляем весовыми сознательно: их часто делают с утяжелением, и
# перевод в «только повторы» отнял бы у пользователя возможность записать вес.
#
# Записанные подходы не трогаем: у каждого свой снимок единицы, и прежняя история
# планки («0 кг × 60») остаётся такой, какой её записали. Трактовать те повторы как
# секунды автоматически нельзя — это домысел за пользователя.

from django.db import migrations

TITLE = "Планка"


def measure_in_time(apps, schema_editor):
    apps.get_model("workouts", "Exercise").objects.filter(
        owner__isnull=True, name__iexact=TITLE
    ).update(measurement="time")


def measure_in_weight(apps, schema_editor):
    apps.get_model("workouts", "Exercise").objects.filter(
        owner__isnull=True, name__iexact=TITLE
    ).update(measurement="weight_reps")


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0009_exercise_measurement"),
    ]

    operations = [
        migrations.RunPython(measure_in_time, measure_in_weight),
    ]
