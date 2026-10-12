"""Средняя мощность кардио: ватты с табло тренажёра или с измерителя.

Мощность — результат занятия, как пульс: форма её не подставляет, и в признак
«на тренажёре» она не входит — заезд с измерителем остаётся маршрутом.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts import excel_import, services
from workouts.models import CardioPart, DeletedWorkout, Sport, Workout
from workouts.tests.factories import (
    CardioPartFactory,
    LocationFactory,
    MachineBrandFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)
from workouts.tests.sheets import build_sheet

pytestmark = pytest.mark.django_db


@pytest.fixture
def bike():
    return SportFactory(name="Велосипед", category=Sport.Category.CARDIO, owner=None)


@pytest.fixture
def park(user):
    return LocationFactory(owner=user, name="Парк у реки")


def recorded_part(user, sport, location=None, *, days_ago=0, **fields):
    workout = WorkoutFactory(
        user=user,
        sport=sport,
        location=location,
        started_at=timezone.now() - timedelta(days=days_ago),
        duration_min=40,
    )
    return CardioPartFactory(workout=workout, sport=sport, duration_min=40, **fields)


def form_data(sport, **overrides):
    data = {
        "sport": sport.pk,
        "date": timezone.localdate().isoformat(),
        "duration_hours": 0,
        "duration_minutes": 40,
        "distance_km": "20",
        "avg_power_w": "180",
        "note": "",
    }
    return data | overrides


# ---------- Ввод ----------


def test_cardio_form_saves_power(client, user, bike):
    client.force_login(user)

    client.post(reverse("cardio_create"), form_data(bike))

    assert CardioPart.objects.get().avg_power_w == 180


def test_zero_power_is_not_given(client, user, bike):
    client.force_login(user)

    client.post(reverse("cardio_create"), form_data(bike, avg_power_w="0"))

    assert CardioPart.objects.get().avg_power_w is None


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("2001", "Мощность — до 2000 Вт."),
        ("-1", "Мощность не бывает отрицательной."),
        ("много", "Мощность — целое число ватт."),
    ],
)
def test_bad_power_is_a_form_error(client, user, bike, value, error):
    client.force_login(user)

    response = client.post(reverse("cardio_create"), form_data(bike, avg_power_w=value))

    assert error in response.content.decode()
    assert not Workout.objects.exists()


def test_plan_has_no_power(client, user, bike):
    client.force_login(user)

    content = client.get(reverse("cardio_prepare"), {"sport": bike.pk}).content.decode()
    client.post(
        reverse("cardio_prepare"), {"sport": bike.pk, "distance_km": "30", "avg_power_w": "200"}
    )

    assert 'name="avg_power_w"' not in content
    assert CardioPart.objects.get().avg_power_w is None


def test_modal_saves_power_and_shows_it_on_edit(client, user, bike):
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    workout = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=workout)
    client.force_login(user)

    client.post(
        reverse("workout_cardio_part", args=[workout.pk]),
        {"sport": bike.pk, "avg_power_w": "150", "duration_hours": 0, "duration_minutes": 15},
    )
    part = CardioPart.objects.get()
    content = client.get(
        reverse("workout_cardio_part_edit", args=[workout.pk, part.pk])
    ).content.decode()

    assert part.avg_power_w == 150
    assert 'value="150"' in content


# ---------- Подстановка ----------


def test_power_is_never_prefilled(client, user, bike, park):
    """Ни из маршрута, ни из тренажёра: это результат, а не свойство места."""
    park.is_default = True
    park.save(update_fields=["is_default"])
    recorded_part(user, bike, park, distance_km=Decimal("42"), avg_power_w=210)
    gym = LocationFactory(owner=user, name="СпортЛайф")
    recorded_part(user, bike, gym, brand=MachineBrandFactory(name="Technogym"), avg_power_w=170)
    client.force_login(user)

    form = client.get(reverse("cardio_create"), {"sport": bike.pk}).content.decode()
    route = client.get(reverse("cardio_route"), {"sport": bike.pk, "location": park.pk})
    machine = client.get(reverse("cardio_machine"), {"sport": bike.pk, "location": gym.pk})

    assert 'value="42"' in form
    assert 'value="210"' not in form
    assert 'value="210"' not in route.content.decode()
    assert 'value="170"' not in machine.content.decode()


def test_power_does_not_turn_route_into_gym(user, bike, park):
    """Заезд с измерителем мощности — всё ещё маршрут: его километры подставляются."""
    ride = recorded_part(user, bike, park, distance_km=Decimal("42"), avg_power_w=210)

    assert services.last_cardio_route(user, bike, park) == ride
    assert services.last_cardio_machine(user, bike, park) is None


# ---------- Вывод ----------


def test_part_row_shows_watts(client, user, bike):
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    workout = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=workout)
    CardioPartFactory(
        workout=workout, sport=bike, distance_km=None, duration_min=20, avg_power_w=160
    )
    client.force_login(user)

    content = client.get(reverse("workout_summary", args=[workout.pk])).content.decode()

    # Пробелы и переводы строк разметки схлопываются, как в браузере; неразрывный — нет.
    assert "0:20 · 160\u00a0Вт" in re.sub(r"[ \n]+", " ", content)


def test_feed_shows_power_when_there_is_no_pace(client, user, bike):
    recorded_part(user, bike, distance_km=None, avg_power_w=175)
    recorded_part(user, bike, days_ago=1, distance_km=Decimal("20"), avg_power_w=190)
    client.force_login(user)

    content = client.get(reverse("workout_history")).content.decode()

    assert "<dt>мощность</dt>" in content
    assert "175\u00a0Вт" in content
    # С дистанцией интенсивность — по-прежнему скорость, а не ватты.
    assert "190\u00a0Вт" not in content
    # Километров у тренажёра нет — прочерк без единицы.
    assert "— км" not in content


# ---------- Excel и корзина ----------


def test_power_column_is_read(user, bike):
    rows = [
        {"date": date(2026, 9, 4), "sport": "Велосипед", "duration": 60, "power": 205},
        {
            "date": date(2026, 9, 5),
            "sport": "Велосипед",
            "duration": 60,
            "power": 0,
            "distance": 20,
        },
    ]

    report = excel_import.import_workbook(user, build_sheet(rows))

    assert report.error_count == 0
    assert sorted(CardioPart.objects.values_list("avg_power_w", flat=True), key=str) == [205, None]


def test_bad_power_cell_is_a_row_error(user, bike):
    rows = [
        {
            "date": date(2026, 9, 4),
            "sport": "Велосипед",
            "duration": 60,
            "distance": 20,
            "power": 2500,
        }
    ]

    report = excel_import.import_workbook(user, build_sheet(rows))

    assert any("Мощность — от 1 до 2000 Вт." in line for line in report.errors)
    assert CardioPart.objects.get().avg_power_w is None


def test_restore_brings_power_back(client, user, bike):
    part = recorded_part(user, bike, avg_power_w=180)
    client.force_login(user)

    client.post(reverse("workout_delete", args=[part.workout.pk]))
    client.post(reverse("workout_restore", args=[DeletedWorkout.objects.get().pk]))

    assert CardioPart.objects.get().avg_power_w == 180
