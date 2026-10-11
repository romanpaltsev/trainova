# Чистка справочника упражнений: удаляются те, что не участвуют ни в одной
# тренировке (решение 11.10.2026).
#
# Справочник ведётся по мере надобности: упражнение появляется, когда его
# правда делают, — «Создать» в справочнике, ввод в живом режиме, заявка в
# общий. Стартовый набор из seed.py, заведённый впрок, уходит вместе со всем
# неиспользованным: и общим, и личным у всех пользователей.
#
# Остаётся упражнение, у которого есть подход в любой тренировке (записанной,
# идущей или черновике — план тоже тренировка), заметка (она живёт только
# внутри тренировки), указанный тренажёр (настроили — значит собираются делать)
# или ожидающая заявка в общий справочник — то, что ждёт решения
# администратора, молча не стираем. Каскадом уходят настройки шага веса и
# решённые заявки удалённых упражнений.
#
# Обратной операции нет: удалённое возвращается только из ночного бэкапа.

from django.db import migrations


def unused(apps):
    Exercise = apps.get_model("workouts", "Exercise")
    return (
        Exercise.objects.filter(sets__isnull=True, notes__isnull=True, machines__isnull=True)
        .exclude(requests__status="pending")
        .distinct()
    )


def delete_unused(apps, schema_editor):
    exercises = unused(apps)
    names = sorted(exercises.values_list("name", flat=True))
    # pk__in, а не delete() на distinct-выборке: Django не удаляет queryset с
    # distinct по джойнам.
    apps.get_model("workouts", "Exercise").objects.filter(
        pk__in=list(exercises.values_list("pk", flat=True))
    ).delete()
    if names:
        # В логе деплоя видно, что ушло.
        print(f"\n  Удалено неиспользованных упражнений: {len(names)} — {', '.join(names)}")


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0083_announce_cardio_machine_excel"),
    ]

    operations = [migrations.RunPython(delete_unused, migrations.RunPython.noop)]
