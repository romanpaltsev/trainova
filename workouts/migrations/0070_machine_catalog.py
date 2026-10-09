import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("workouts", "0069_announce_exercise_machine"),
    ]

    operations = [
        migrations.CreateModel(
            name="MachineBrand",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=60, verbose_name="название")),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        help_text="Пусто — глобальная запись, видна всем пользователям.",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(class)ss",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="владелец",
                    ),
                ),
            ],
            options={
                "verbose_name": "производитель тренажёров",
                "verbose_name_plural": "производители тренажёров",
                "ordering": ["name"],
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        django.db.models.functions.text.Lower("name"),
                        models.F("owner"),
                        name="unique_machine_brand_per_owner",
                        violation_error_message="Такой производитель у вас уже есть.",
                    ),
                    models.UniqueConstraint(
                        django.db.models.functions.text.Lower("name"),
                        condition=models.Q(("owner__isnull", True)),
                        name="unique_global_machine_brand",
                        violation_error_message="Общий производитель с таким названием уже есть.",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="MachineModel",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=80, verbose_name="название")),
                (
                    "brand",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="models",
                        to="workouts.machinebrand",
                        verbose_name="производитель",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        help_text="Пусто — глобальная запись, видна всем пользователям.",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(class)ss",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="владелец",
                    ),
                ),
            ],
            options={
                "verbose_name": "модель тренажёра",
                "verbose_name_plural": "модели тренажёров",
                "ordering": ["name"],
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        models.F("brand"),
                        django.db.models.functions.text.Lower("name"),
                        models.F("owner"),
                        name="unique_machine_model_per_owner",
                        violation_error_message="Такая модель у вас уже есть.",
                    ),
                    models.UniqueConstraint(
                        models.F("brand"),
                        django.db.models.functions.text.Lower("name"),
                        condition=models.Q(("owner__isnull", True)),
                        name="unique_global_machine_model",
                        violation_error_message="Общая модель с таким названием уже есть.",
                    ),
                ],
            },
        ),
        # Ссылки на справочник — пока рядом с текстом и под временными именами:
        # следующая миграция переносит текст, третья убирает старые поля.
        migrations.AddField(
            model_name="exercisemachine",
            name="brand_ref",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="+",
                to="workouts.machinebrand",
            ),
        ),
        migrations.AddField(
            model_name="exercisemachine",
            name="model_ref",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="+",
                to="workouts.machinemodel",
            ),
        ),
    ]
