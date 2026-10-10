# Свой цвет у видов спорта вне четвёрки «Силовая, Велосипед, Бег, Лыжи».
#
# Раньше свой кардио-вид красился цветом бега, свой силовой — цветом силовой, и
# в чипах, ленте и графике их было не отличить. Поле — слот дополнительной
# палитры; уже существующим видам он раздаётся здесь тем же правилом, что и
# новым в Sport.save (models.free_palette_slot): в порядке создания, наименее
# занятый среди видимых вместе.

from collections import Counter

from django.db import migrations, models

# Копии models.GLOBAL_SPORT_COLORS и SPORT_PALETTE_SIZE: миграция не импортирует
# живой код, он со временем меняется.
KNOWN = {"силовая", "велосипед", "бег", "лыжи"}
PALETTE_SIZE = 4


def assign_palette(apps, schema_editor):
    Sport = apps.get_model("workouts", "Sport")
    sports = [s for s in Sport.objects.order_by("pk") if s.name.strip().lower() not in KNOWN]
    global_used = Counter()
    own_used = {}
    for sport in sports:
        if sport.owner_id is None:
            used = global_used
        else:
            used = global_used + own_used.setdefault(sport.owner_id, Counter())
        slot = min(range(PALETTE_SIZE), key=lambda n: (used[n], n))
        if sport.owner_id is None:
            global_used[slot] += 1
        else:
            own_used[sport.owner_id][slot] += 1
        sport.palette = slot
        sport.save(update_fields=["palette"])


class Migration(migrations.Migration):
    dependencies = [
        ("workouts", "0077_announce_catalog_requests"),
    ]

    operations = [
        migrations.AddField(
            model_name="sport",
            name="palette",
            field=models.PositiveSmallIntegerField(
                blank=True, editable=False, null=True, verbose_name="цвет"
            ),
        ),
        migrations.RunPython(assign_palette, migrations.RunPython.noop),
    ]
