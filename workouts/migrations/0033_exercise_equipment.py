# Снаряд — вторая ось справочника упражнений.
#
# Свободный текст, а не choices: набор снарядов у каждого зала свой, а у личных
# упражнений он вообще произвольный — то же решение, что у группы мышц. Пустая
# строка значит «не указан»; NULL здесь не нужен, двух видов пустоты у текстового
# фасета не бывает.
#
# Значения проставляет следующая миграция (глобальным записям) и seed (новым).
# Откат снимет колонку вместе со всеми значениями.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workouts', '0032_announce_mixed_workouts'),
    ]

    operations = [
        migrations.AddField(
            model_name='exercise',
            name='equipment',
            field=models.CharField(blank=True, max_length=30, verbose_name='снаряд'),
        ),
    ]
