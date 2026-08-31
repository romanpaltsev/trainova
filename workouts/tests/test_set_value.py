"""Ручной ввод значения подхода: тап по числу вместо серии тапов по «+».

Значение приходит абсолютным, разбирает его сервер: «82,5» и «82.5» — одно и то
же, время принимается и как «1:30», и как «90». Выход за границы зажимается,
мусор отвергается с человеческим текстом.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from workouts.models import Exercise
from workouts.tests.factories import (
    ExerciseFactory,
    StrengthSetFactory,
    TimeSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db

MEASURE = Exercise.Measurement


def active(user, **kwargs):
    return WorkoutFactory(user=user, duration_min=None, **kwargs)


def set_value(client, row, field, value):
    return client.post(reverse("set_value", args=[row.pk]), {"field": field, "value": value})


# ---------- Вес ----------


@pytest.mark.parametrize("text", ["82,5", "82.5", " 82,50 "], ids=["запятая", "точка", "пробелы"])
def test_weight_accepts_comma_and_dot(client, user, text):
    row = StrengthSetFactory(workout=active(user), set_number=1, weight_kg=70, done=False)

    client.force_login(user)
    response = set_value(client, row, "weight_kg", text)

    row.refresh_from_db()
    assert row.weight_kg == Decimal("82.50")
    assert response.content.decode() == "82,5"


def test_weight_above_limit_is_clamped(client, user):
    """Человек написал 1000 — получит 999,99, а не ошибку на пустом месте."""
    row = StrengthSetFactory(workout=active(user), set_number=1, weight_kg=70, done=False)

    client.force_login(user)
    set_value(client, row, "weight_kg", "1000")

    row.refresh_from_db()
    assert row.weight_kg == Decimal("999.99")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param("abc", "Вес — это число", id="буквы"),
        pytest.param("-5", "отрицательным", id="минус"),
        pytest.param("", "Введите значение", id="пусто"),
    ],
)
def test_bad_weight_is_rejected_with_a_readable_message(client, user, text, message):
    row = StrengthSetFactory(workout=active(user), set_number=1, weight_kg=70, done=False)

    client.force_login(user)
    response = set_value(client, row, "weight_kg", text)

    assert response.status_code == 400
    assert message in response.content.decode()
    row.refresh_from_db()
    assert row.weight_kg == Decimal("70.00")


# ---------- Повторы ----------


def test_reps_are_saved(client, user):
    row = StrengthSetFactory(workout=active(user), set_number=1, reps=8, done=False)

    client.force_login(user)
    response = set_value(client, row, "reps", "12")

    row.refresh_from_db()
    assert row.reps == 12
    assert response.content.decode() == "12"


def test_reps_reject_non_integer(client, user):
    row = StrengthSetFactory(workout=active(user), set_number=1, reps=8, done=False)

    client.force_login(user)
    response = set_value(client, row, "reps", "12,5")

    assert response.status_code == 400
    row.refresh_from_db()
    assert row.reps == 8


# ---------- Время ----------


@pytest.mark.parametrize(
    ("text", "seconds", "shown"),
    [
        pytest.param("1:30", 90, "1:30", id="минуты-с-секундами"),
        pytest.param("90", 90, "1:30", id="просто-секунды"),
        pytest.param("2:05", 125, "2:05", id="с-ведущим-нулём"),
    ],
)
def test_time_accepts_both_notations(client, user, text, seconds, shown):
    plank = ExerciseFactory(name="Планка", measurement=MEASURE.TIME)
    row = TimeSetFactory(
        workout=active(user), exercise=plank, set_number=1, duration_sec=60, done=False
    )

    client.force_login(user)
    response = set_value(client, row, "duration_sec", text)

    row.refresh_from_db()
    assert row.duration_sec == seconds
    assert response.content.decode() == shown


def test_time_with_impossible_seconds_is_rejected(client, user):
    """«1:70» — это опечатка, а не 130 секунд: угадывать за пользователя не надо."""
    plank = ExerciseFactory(name="Планка", measurement=MEASURE.TIME)
    row = TimeSetFactory(
        workout=active(user), exercise=plank, set_number=1, duration_sec=60, done=False
    )

    client.force_login(user)
    response = set_value(client, row, "duration_sec", "1:70")

    assert response.status_code == 400
    row.refresh_from_db()
    assert row.duration_sec == 60


# ---------- Единица упражнения и изоляция ----------


def test_field_from_another_unit_is_rejected(client, user):
    plank = ExerciseFactory(name="Планка", measurement=MEASURE.TIME)
    row = TimeSetFactory(workout=active(user), exercise=plank, set_number=1, done=False)

    client.force_login(user)
    response = set_value(client, row, "weight_kg", "80")

    assert response.status_code == 400
    row.refresh_from_db()
    assert row.weight_kg == 0


def test_unknown_field_is_rejected(client, user):
    row = StrengthSetFactory(workout=active(user), set_number=1, done=False)

    client.force_login(user)

    assert set_value(client, row, "height", "180").status_code == 400


def test_foreign_set_is_not_reachable(client, user, other_user):
    row = StrengthSetFactory(workout=active(other_user), set_number=1, weight_kg=70, done=False)

    client.force_login(user)
    response = set_value(client, row, "weight_kg", "200")

    assert response.status_code == 404
    row.refresh_from_db()
    assert row.weight_kg == Decimal("70.00")


def test_done_set_is_immutable(client, user):
    row = StrengthSetFactory(workout=active(user), set_number=1, weight_kg=70, done=True)

    client.force_login(user)
    response = set_value(client, row, "weight_kg", "200")

    assert response.status_code == 404
    row.refresh_from_db()
    assert row.weight_kg == Decimal("70.00")


def test_set_of_finished_workout_is_immutable(client, user):
    row = StrengthSetFactory(workout=WorkoutFactory(user=user), set_number=1, weight_kg=70)

    client.force_login(user)

    assert set_value(client, row, "weight_kg", "200").status_code == 404


def test_value_can_be_typed_in_a_draft(client, user):
    draft = WorkoutFactory(user=user, started_at=None, duration_min=None)
    row = StrengthSetFactory(workout=draft, set_number=1, weight_kg=0, done=False)

    client.force_login(user)
    set_value(client, row, "weight_kg", "60")

    row.refresh_from_db()
    assert row.weight_kg == Decimal("60.00")


# ---------- Экран ----------


def test_live_screen_offers_manual_input(client, user):
    workout = active(user)
    row = StrengthSetFactory(workout=workout, set_number=1, weight_kg=80, reps=8, done=False)

    client.force_login(user)
    content = client.get(reverse("workout_live", args=[workout.pk])).content.decode()

    # Поле ввода и правила предсказания приезжают вместе со степпером.
    assert reverse("set_value", args=[row.pk]) in content
    assert 'data-step="2.5"' in content
    assert 'data-max="999.99"' in content
    assert 'data-format="decimal"' in content
    assert 'inputmode="decimal"' in content
    assert "не сохранено — проверьте связь" in content
