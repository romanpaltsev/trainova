# «Руки» и «Ноги» общих упражнений — по мышцам (решение 16.10.2026).
#
# «Руки» не говорили, бицепс это был или трицепс, — в подписи тренировки и в
# справочнике приходилось вчитываться в названия. Общие упражнения раскладываем
# по подстрокам названия, первое совпадение побеждает; неузнанное остаётся как
# было — администратор доправит его чипом на странице упражнения. Личные не
# трогаем: это данные пользователя, свои он переделает сам.
#
# Обратного хода нет (noop): разложенное назад без догадок не собрать —
# «Бицепс» мог появиться и руками администратора.

from django.db import migrations

# (старая группа, [(новая группа, подстроки названия)]) — порядок важен:
# «сгибания запястий» — предплечья, а не бицепс; «сгибание ног» — бицепс бедра.
RULES = {
    "руки": [
        ("Предплечья", ("запяст", "предплеч", "кистев")),
        ("Трицепс", ("трицепс", "французск", "разгибан", "узким хватом", "брусь")),
        ("Бицепс", ("бицепс", "сгибан", "молот", "концентрир", "скотт")),
    ],
    "ноги": [
        ("Икры", ("носк", "икр")),
        ("Ягодицы", ("ягодич", "мостик", "отведен")),
        ("Бицепс бедра", ("сгибан", "румынск", "мёртв", "мертв", "прямых ногах")),
        ("Квадрицепс", ("присед", "жим ног", "выпад", "разгибан", "гакк", "хакк", "болгарск")),
    ],
}


def split_group(name, group):
    """Новая группа упражнения или None — правила не узнали название."""
    lowered = name.casefold()
    for new_group, needles in RULES.get(group.casefold(), ()):
        if any(needle in lowered for needle in needles):
            return new_group
    return None


def split(apps, schema_editor):
    # Регистр сравниваем в Python, а не iexact: регистронезависимость кириллицы
    # в Postgres зависит от локали базы. Общих упражнений — десятки, не тысячи.
    exercises = apps.get_model("workouts", "Exercise").objects
    for exercise in exercises.filter(owner__isnull=True).exclude(muscle_group=""):
        new_group = split_group(exercise.name, exercise.muscle_group)
        if new_group is not None:
            exercise.muscle_group = new_group
            exercise.save(update_fields=["muscle_group"])


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0099_slang_changelog"),
    ]

    operations = [migrations.RunPython(split, migrations.RunPython.noop)]
