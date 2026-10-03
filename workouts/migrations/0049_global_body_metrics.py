# Общие параметры тела: вес, рост, процент жира, обхваты и пульс в покое.
#
# Живут здесь, а не в seed.py, — исключение из правила справочника упражнений:
# миграции на проде применяются каждым деплоем, а seed запускают руками, и без
# общих параметров раздел «Мои замеры» был бы пуст. Список один — здесь; seed
# его не дублирует. Новый общий параметр или переименование — новой
# data-миграцией, как 0034_precise_exercise_names.
#
# Порядок списка — порядок на экране: общие параметры сортируются по id.

from django.db import migrations

GLOBAL_METRICS = [
    ("Вес", "кг"),
    ("Рост", "см"),
    ("Процент жира", "%"),
    ("Талия", "см"),
    ("Грудь", "см"),
    ("Бёдра", "см"),
    ("Бицепс", "см"),
    ("Пульс в покое", "уд/мин"),
]


def add_metrics(apps, schema_editor):
    metric_model = apps.get_model("workouts", "BodyMetric")
    for name, unit in GLOBAL_METRICS:
        metric_model.objects.get_or_create(name=name, owner=None, defaults={"unit": unit})


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0048_body_measurements"),
    ]

    # Обратно — ничего: прямой шаг повторяем, а откат 0048 удалит таблицы целиком.
    operations = [migrations.RunPython(add_metrics, migrations.RunPython.noop)]
