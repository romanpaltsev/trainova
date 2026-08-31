"""Свой шаг веса у каждого упражнения.

Шаг — личная настройка: у приседа со штангой он один, у гантельного жима другой,
и у приглашённого друга свой. Поэтому настраивается и для глобальных упражнений,
которые сам справочник править не даёт.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from workouts.models import (
    DEFAULT_WEIGHT_STEP,
    WEIGHT_STEP_CHOICES,
    Exercise,
    ExerciseSettings,
)
from workouts.tests.factories import (
    ExerciseFactory,
    ExerciseSettingsFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def planned_set(user, exercise, **overrides):
    workout = overrides.pop("workout", None) or WorkoutFactory(user=user, duration_min=None)
    return StrengthSetFactory(
        **{"workout": workout, "exercise": exercise, "set_number": 1, "done": False} | overrides
    )


def set_step(client, exercise, **data):
    return client.post(reverse("exercise_weight_step", args=[exercise.pk]), data)


def adjust(client, row, direction="up"):
    return client.post(
        reverse("set_adjust", args=[row.pk]), {"field": "weight_kg", "dir": direction}
    )


# ---------- шаг применяется к степперу ----------


def test_default_step_is_used_when_nothing_configured(client, user):
    client.force_login(user)
    row = planned_set(user, ExerciseFactory(), weight_kg=80)

    assert adjust(client, row).content.decode() == "82,5"
    row.refresh_from_db()
    assert row.weight_kg == Decimal("82.50")


def test_configured_step_changes_tap_size(client, user):
    client.force_login(user)
    exercise = ExerciseFactory(name="Присед с гантелями")
    ExerciseSettingsFactory(user=user, exercise=exercise, weight_step=Decimal("0.5"))
    row = planned_set(user, exercise, weight_kg=20)

    assert adjust(client, row).content.decode() == "20,5"
    assert adjust(client, row, "down").content.decode() == "20"


def test_step_is_per_exercise_within_one_workout(client, user):
    """Два упражнения одной тренировки шагают по-своему — ради этого всё и затевалось."""
    client.force_login(user)
    workout = WorkoutFactory(user=user, duration_min=None)
    barbell = ExerciseFactory(name="Приседания со штангой")
    dumbbells = ExerciseFactory(name="Присед с гантелями")
    ExerciseSettingsFactory(user=user, exercise=barbell, weight_step=Decimal("5"))
    ExerciseSettingsFactory(user=user, exercise=dumbbells, weight_step=Decimal("0.5"))
    barbell_set = planned_set(user, barbell, workout=workout, weight_kg=100, set_number=1)
    dumbbell_set = planned_set(user, dumbbells, workout=workout, weight_kg=20, set_number=2)

    assert adjust(client, barbell_set).content.decode() == "105"
    assert adjust(client, dumbbell_set).content.decode() == "20,5"


def test_step_reaches_the_live_screen(client, user):
    """Кнопка, подпись под ней и предсказание на клиенте берут один и тот же шаг."""
    client.force_login(user)
    exercise = ExerciseFactory(name="Присед с гантелями")
    ExerciseSettingsFactory(user=user, exercise=exercise, weight_step=Decimal("0.5"))
    row = planned_set(user, exercise, weight_kg=20)

    html = client.get(reverse("workout_live", args=[row.workout.pk])).content.decode()

    assert 'data-step="0.50"' in html  # предсказание значения на клиенте
    assert "Плюс 0,5 кг" in html  # озвучка кнопки для скринридера
    assert "Минус 0,5 кг" in html


def test_time_and_reps_keep_common_steps(client, user):
    """Настраивается только вес: повторы по 1, время по 15 секунд."""
    client.force_login(user)
    exercise = ExerciseFactory()
    ExerciseSettingsFactory(user=user, exercise=exercise, weight_step=Decimal("5"))
    row = planned_set(user, exercise, reps=8)

    response = client.post(reverse("set_adjust", args=[row.pk]), {"field": "reps", "dir": "up"})

    assert response.content.decode() == "9"


# ---------- настройка на странице упражнения ----------


def test_step_is_configurable_for_global_exercise(client, user):
    """Ключевой сценарий: «Приседания со штангой» — глобальные, но шаг свой."""
    client.force_login(user)
    global_exercise = ExerciseFactory(name="Приседания со штангой", owner=None)

    response = set_step(client, global_exercise, weight_step="5")

    assert response.status_code == 200
    settings = ExerciseSettings.objects.get(user=user, exercise=global_exercise)
    assert settings.weight_step == Decimal("5.00")
    assert global_exercise.owner_id is None  # само упражнение не тронуто


def test_own_value_beats_chip(client, user):
    client.force_login(user)
    exercise = ExerciseFactory()

    set_step(client, exercise, weight_step="2.5", weight_step_own="0,25")

    assert ExerciseSettings.objects.get(user=user).weight_step == Decimal("0.25")


def test_step_is_updated_not_duplicated(client, user):
    client.force_login(user)
    exercise = ExerciseFactory()

    set_step(client, exercise, weight_step="5")
    set_step(client, exercise, weight_step="1")

    assert ExerciseSettings.objects.filter(user=user, exercise=exercise).count() == 1
    assert ExerciseSettings.objects.get(user=user).weight_step == Decimal("1.00")


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "60", "0,1", "много", ""],
    ids=["ноль", "минус", "больше макс", "меньше мин", "мусор", "пусто"],
)
def test_invalid_step_is_rejected_with_message(client, user, value):
    client.force_login(user)
    exercise = ExerciseFactory()

    response = set_step(client, exercise, weight_step_own=value)

    assert response.status_code == 200
    assert "invalid-feedback" in response.content.decode()
    assert not ExerciseSettings.objects.exists()


def test_block_is_absent_for_exercises_without_weight(client, user):
    client.force_login(user)
    plank = ExerciseFactory(name="Планка", measurement=Exercise.Measurement.TIME)

    html = client.get(reverse("exercise_detail", args=[plank.pk])).content.decode()

    assert "weight-step-choice" not in html
    assert set_step(client, plank, weight_step="5").status_code == 400


def test_current_step_is_shown_on_exercise_page(client, user):
    client.force_login(user)
    exercise = ExerciseFactory()
    ExerciseSettingsFactory(user=user, exercise=exercise, weight_step=Decimal("1.25"))

    html = client.get(reverse("exercise_detail", args=[exercise.pk])).content.decode()

    assert "Сейчас 1,25 кг за тап" in html


# ---------- изоляция ----------


def test_step_of_one_user_does_not_leak_to_another(client, user, other_user):
    exercise = ExerciseFactory(name="Приседания со штангой", owner=None)
    ExerciseSettingsFactory(user=other_user, exercise=exercise, weight_step=Decimal("5"))
    client.force_login(user)
    row = planned_set(user, exercise, weight_kg=100)

    assert adjust(client, row).content.decode() == "102,5"  # своё умолчание, не чужие 5
    html = client.get(reverse("exercise_detail", args=[exercise.pk])).content.decode()
    assert f"Сейчас {str(DEFAULT_WEIGHT_STEP).replace('.', ',')} кг за тап" in html


def test_other_users_personal_exercise_is_not_configurable(client, user, other_user):
    client.force_login(user)
    alien = ExerciseFactory(name="Секретное упражнение", owner=other_user)

    assert set_step(client, alien, weight_step="5").status_code == 404
    assert not ExerciseSettings.objects.exists()


def test_anonymous_cannot_configure_step(client, user):
    exercise = ExerciseFactory()

    response = set_step(client, exercise, weight_step="5")

    assert response.status_code == 302
    assert not ExerciseSettings.objects.exists()


def test_settings_are_removed_with_the_exercise(client, user):
    """CASCADE: настройка без упражнения бессмысленна и не держит его удаление."""
    exercise = ExerciseFactory(owner=user)
    ExerciseSettingsFactory(user=user, exercise=exercise)

    exercise.delete()

    assert not ExerciseSettings.objects.exists()


def test_chip_wins_over_previously_saved_custom_value(client, user):
    """Чип должен применяться, даже когда в поле «свой» стоит прежнее значение.

    Сторож против регрессии: пока чипы и поле «свой» жили в общей форме, клик по
    чипу отправлял вместе с ним и прежнее значение поля — а оно перебивает чип,
    и выбор не применялся. Поэтому каждый чип постит себя сам, без формы.
    """
    client.force_login(user)
    exercise = ExerciseFactory()
    set_step(client, exercise, weight_step_own="0,25")

    html = client.get(reverse("exercise_detail", args=[exercise.pk])).content.decode()
    # Блок шага заканчивается там, где начинается следующий — «Группа мышц».
    block = html[html.index('id="weight-step-choice"') : html.index('id="muscle-group-choice"')]

    assert "<form" not in block, "чипы и поле «свой» не должны делить одну форму"
    assert block.count("hx-post") >= len(WEIGHT_STEP_CHOICES), "каждый чип постит себя сам"
    # И поведение целиком: чип после сохранённого своего значения применяется.
    set_step(client, exercise, weight_step="5")
    assert ExerciseSettings.objects.get(user=user).weight_step == Decimal("5.00")
