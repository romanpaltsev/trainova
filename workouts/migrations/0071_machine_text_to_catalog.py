"""Текст производителя и модели у ExerciseMachine становится записями справочника.

Производитель ищется среди общих (без учёта регистра), иначе среди своих
владельца строки, иначе заводится своим; модель — так же, у этого
производителя. Строка без производителя получает свой «Без производителя»:
в новой схеме производитель обязателен, а модель терять нельзя.
"""

from django.db import migrations

NO_BRAND = "Без производителя"


def find_or_create(manager, user_id, name, **extra):
    found = manager.filter(owner__isnull=True, name__iexact=name, **extra).first()
    if found is None:
        found = manager.filter(owner_id=user_id, name__iexact=name, **extra).first()
    if found is None:
        found = manager.create(owner_id=user_id, name=name, **extra)
    return found


def text_to_catalog(apps, schema_editor):
    ExerciseMachine = apps.get_model("workouts", "ExerciseMachine")
    MachineBrand = apps.get_model("workouts", "MachineBrand")
    MachineModel = apps.get_model("workouts", "MachineModel")
    for machine in ExerciseMachine.objects.all():
        brand = find_or_create(MachineBrand.objects, machine.user_id, machine.brand or NO_BRAND)
        model = None
        if machine.model:
            model = find_or_create(
                MachineModel.objects, machine.user_id, machine.model, brand=brand
            )
        machine.brand_ref = brand
        machine.model_ref = model
        machine.save(update_fields=["brand_ref", "model_ref"])


def catalog_to_text(apps, schema_editor):
    ExerciseMachine = apps.get_model("workouts", "ExerciseMachine")
    for machine in ExerciseMachine.objects.select_related("brand_ref", "model_ref"):
        machine.brand = machine.brand_ref.name if machine.brand_ref_id else ""
        machine.model = machine.model_ref.name if machine.model_ref_id else ""
        machine.save(update_fields=["brand", "model"])


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0070_machine_catalog"),
    ]

    operations = [migrations.RunPython(text_to_catalog, catalog_to_text)]
