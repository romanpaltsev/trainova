# Новость о фото тренажёров — сленгом качалки, как теперь пишем «Что нового».
#
# Новость 0097 к этому времени уже вышла: правим её на месте, а не публикуем
# вторую (приём 0060). published_at не трогаем — точка «есть новое» второй раз
# не загорится. Запись, которую успели переименовать в админке, старым
# заголовком не найдётся и останется как есть.

from django.db import migrations

OLD_TITLE = "Тренажёр теперь узнать в лицо"
OLD_BODY = (
    "У моделей тренажёров появились фото. Нажмите на фотоаппарат в окне «Тренажёр» "
    "прямо у снаряда или на «Моих тренажёрах» — и в следующий раз тот самый жим ногами "
    "найдётся по картинке, а не по загадочному «Selection 900». Снимок сам уменьшится "
    "и забудет, где его сделали, а фото своей модели видите только вы — пока не "
    "предложите её в общий список."
)
TITLE = "Железо теперь с лицом"
BODY = (
    "Завезли фотки тренажёров! У своей модели жмёшь на фотик прямо у железа — в окне "
    "«Тренажёр» — или в «Моих тренажёрах», и в следующий раз тот самый жим ногами "
    "палится по картинке, а не по шифровке «Selection 900». Фотка сама ужмётся и "
    "забудет, где ты её щёлкнул, а видишь её только ты — пока не закинешь модель в "
    "общий список. Селфи с бицухой не прокатит: в кадре только железо."
)


def rewrite(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    entries.filter(title=OLD_TITLE).update(title=TITLE, body=BODY)


def rewrite_back(apps, schema_editor):
    entries = apps.get_model("workouts", "ChangelogEntry").objects
    entries.filter(title=TITLE).update(title=OLD_TITLE, body=OLD_BODY)


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0097_announce_machine_photos"),
    ]

    operations = [migrations.RunPython(rewrite, rewrite_back)]
