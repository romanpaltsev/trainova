import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0071_machine_text_to_catalog"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="exercisemachine", name="machine_is_not_empty"),
        migrations.RemoveField(model_name="exercisemachine", name="brand"),
        migrations.RemoveField(model_name="exercisemachine", name="model"),
        migrations.RenameField(model_name="exercisemachine", old_name="brand_ref", new_name="brand"),
        migrations.RenameField(model_name="exercisemachine", old_name="model_ref", new_name="model"),
        migrations.AlterField(
            model_name="exercisemachine",
            name="brand",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="exercise_machines",
                to="workouts.machinebrand",
                verbose_name="производитель",
            ),
        ),
        migrations.AlterField(
            model_name="exercisemachine",
            name="model",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="exercise_machines",
                to="workouts.machinemodel",
                verbose_name="модель",
            ),
        ),
    ]
