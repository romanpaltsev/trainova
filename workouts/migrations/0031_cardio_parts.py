"""CardioDetails → CardioPart: кардио становится частью тренировки, а не её сутью.

Тренировка превращается в контейнер: силовая часть — это её подходы, кардио-часть
— отдельная строка со своим видом спорта и своим временем. Благодаря этому одно
занятие «разминка бегом → штанга → заминка на велосипеде» записывается одной
тренировкой.

Миграция переименовывает модель, а не создаёт новую: данные кардио-истории
переезжают вместе с таблицей. Бэкфилл проставляет частям вид спорта и
длительность их тренировки — до появления частей это было одно и то же, поэтому
значения точные, а не выдуманные. После него скорость и темп считаются по
колонкам части и больше не могут соврать на смешанной тренировке.
"""

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def fill_sport_and_duration(apps, schema_editor):
    part_model = apps.get_model("workouts", "CardioPart")
    workout_model = apps.get_model("workouts", "Workout")
    # Одним UPDATE на всю таблицу. Подзапросом, а не F("workout__sport_id"):
    # ссылки через JOIN в UPDATE Django не принимает.
    owner = workout_model.objects.filter(pk=models.OuterRef("workout_id"))
    part_model.objects.update(
        sport_id=models.Subquery(owner.values("sport_id")[:1]),
        duration_min=models.Subquery(owner.values("duration_min")[:1]),
    )


def clear_sport_and_duration(apps, schema_editor):
    # Откат: поля исчезают вместе со схемой, данных терять нечего.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0030_announce_excel_export"),
    ]

    operations = [
        migrations.RenameModel(old_name="CardioDetails", new_name="CardioPart"),
        migrations.AlterModelOptions(
            name="cardiopart",
            options={
                "ordering": ["id"],
                "verbose_name": "кардио-часть",
                "verbose_name_plural": "кардио-части",
            },
        ),
        # OneToOne → FK: частей у тренировки может быть несколько, уникальность
        # по workout_id снимается.
        migrations.AlterField(
            model_name="cardiopart",
            name="workout",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="cardio_parts",
                to="workouts.workout",
                verbose_name="тренировка",
            ),
        ),
        # Сначала nullable — иначе колонку некуда положить существующим строкам;
        # обязательной она становится после бэкфилла, последней операцией.
        migrations.AddField(
            model_name="cardiopart",
            name="sport",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cardio_parts",
                to="workouts.sport",
                verbose_name="вид спорта",
            ),
        ),
        migrations.AddField(
            model_name="cardiopart",
            name="duration_min",
            field=models.PositiveIntegerField(
                blank=True, null=True, verbose_name="длительность, мин"
            ),
        ),
        migrations.AlterField(
            model_name="cardiopart",
            name="distance_km",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=6,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
                verbose_name="дистанция, км",
            ),
        ),
        migrations.RunPython(fill_sport_and_duration, clear_sport_and_duration),
        migrations.AlterField(
            model_name="cardiopart",
            name="sport",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cardio_parts",
                to="workouts.sport",
                verbose_name="вид спорта",
            ),
        ),
    ]
