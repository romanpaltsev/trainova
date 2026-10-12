"""Маршрут кардио: набор высоты и подстановка «как в прошлый раз здесь».

У маршрута километры и метры вверх — свойство места, поэтому форма подставляет
их из прошлой части того же вида спорта в том же месте. Места, где этим видом
спорта занимались на тренажёре, маршрутом не считаются: на дорожке дистанция —
результат, и подставлять её нельзя.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from workouts import services
from workouts.models import CardioPart, DeletedWorkout, Sport, Workout
from workouts.tests.factories import (
    CardioPartFactory,
    LocationFactory,
    MachineBrandFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def run():
    return SportFactory(name="Бег", category=Sport.Category.CARDIO, owner=None)


@pytest.fixture
def park(user):
    return LocationFactory(owner=user, name="Парк у реки")


def recorded_part(user, sport, location, *, days_ago=0, **fields):
    workout = WorkoutFactory(
        user=user,
        sport=sport,
        location=location,
        started_at=timezone.now() - timedelta(days=days_ago),
        duration_min=40,
    )
    return CardioPartFactory(workout=workout, sport=sport, duration_min=40, **fields)


def route(client, sport, location=None, **params):
    query = {"sport": sport.pk, "location": location.pk if location else ""} | params
    return client.get(reverse("cardio_route"), query)


# ---------- Источник подстановки ----------


def test_route_is_last_part_of_sport_in_same_place(user, other_user, run, park):
    recorded_part(user, run, park, days_ago=7, distance_km=Decimal("5"))
    last = recorded_part(
        user, run, park, days_ago=1, distance_km=Decimal("7.4"), elevation_gain_m=60
    )
    recorded_part(user, run, LocationFactory(owner=user), distance_km=Decimal("12"))
    recorded_part(user, SportFactory(category=Sport.Category.CARDIO), park, distance_km=30)
    recorded_part(other_user, run, None, distance_km=Decimal("21.1"))

    assert services.last_cardio_route(user, run, park) == last


def test_route_ignores_drafts_and_others(user, other_user, run, park):
    foreign_park = LocationFactory(owner=other_user)
    recorded_part(other_user, run, foreign_park, distance_km=Decimal("9"))
    draft = WorkoutFactory(user=user, sport=run, location=park, started_at=None, duration_min=None)
    CardioPartFactory(workout=draft, sport=run, distance_km=Decimal("10"))

    assert services.last_cardio_route(user, run, park) is None
    # Чужое место не становится маршрутом и по прямому id.
    assert services.last_cardio_route(user, run, foreign_park) is None


def test_route_skips_parts_without_distance_and_elevation(user, run, park):
    """«Пробежал, не мерил» не стирает километры маршрута."""
    known = recorded_part(user, run, park, days_ago=3, distance_km=Decimal("7.4"))
    recorded_part(user, run, park, days_ago=1, distance_km=None)

    assert services.last_cardio_route(user, run, park) == known


def test_equal_start_is_won_by_later_part(user, run, park):
    first = recorded_part(user, run, park, distance_km=Decimal("5"))
    second = CardioPartFactory(workout=first.workout, sport=run, distance_km=Decimal("6"))

    assert services.last_cardio_route(user, run, park) == second


def test_place_with_machine_is_not_a_route(user, run, park):
    """Хоть раз здесь был тренажёр — это зал: дистанция там результат, а
    подставляет «Тренажёр» — ровно как до появления маршрутов."""
    recorded_part(user, run, park, days_ago=5, brand=MachineBrandFactory(name="Technogym"))
    recorded_part(user, run, park, days_ago=1, distance_km=Decimal("3.2"))

    assert services.last_cardio_route(user, run, park) is None
    assert services.last_cardio_machine(user, run, park).brand.name == "Technogym"


def test_route_needs_a_place(user, run):
    recorded_part(user, run, None, distance_km=Decimal("5"))

    assert services.last_cardio_route(user, run, None) is None


# ---------- Эндпоинт блока ----------


def test_block_comes_filled_as_last_time(client, user, run, park):
    recorded_part(user, run, park, distance_km=Decimal("7.4"), elevation_gain_m=60)
    client.force_login(user)

    content = route(client, run, park).content.decode()

    assert 'value="7.4"' in content
    assert 'value="60"' in content
    assert "Как в прошлый раз в «Парк у реки»" in content
    assert 'name="route_prefilled"' in content


def test_typed_route_is_not_overwritten(client, user, run, park):
    recorded_part(user, run, park, distance_km=Decimal("7.4"))
    client.force_login(user)

    assert route(client, run, park, distance_km="10").status_code == 204
    replaced = route(client, run, park, distance_km="10", route_prefilled="1")
    assert 'value="7.4"' in replaced.content.decode()


def test_nothing_to_prefill_is_204(client, user, run, park):
    client.force_login(user)

    assert route(client, run, park).status_code == 204


def test_place_without_history_clears_prefilled(client, user, run, park):
    """Сменили «Парк» на место без истории — подставленное из «Парка» уходит."""
    client.force_login(user)

    response = route(client, run, park, distance_km="7.40", route_prefilled="1")

    assert response.status_code == 200
    assert 'value="7.4"' not in response.content.decode()


def test_typed_new_place_beats_checked_chip(client, user, run, park):
    """Новое название перебивает отмеченный чип — как при сохранении формы."""
    recorded_part(user, run, park, distance_km=Decimal("7.4"))
    client.force_login(user)

    assert route(client, run, park, location_own="Стадион").status_code == 204
    # Набранное совпало со своим местом — это оно, и у него есть прошлое.
    typed = route(client, run, location_own="парк у реки")
    assert 'value="7.4"' in typed.content.decode()


def test_new_place_text_hides_machine_of_checked_chip(client, user, run, park):
    """То же правило у «Тренажёра»: новое место не наследует тренажёр чипа."""
    recorded_part(user, run, park, brand=MachineBrandFactory(name="Technogym"))
    client.force_login(user)

    query = {"sport": run.pk, "location": park.pk, "location_own": "Стадион"}
    content = client.get(reverse("cardio_machine"), query).content.decode()

    assert 'value="Technogym"' not in content.split("<datalist")[0]


def test_foreign_place_gives_nothing(client, user, other_user, run):
    foreign = LocationFactory(owner=other_user)
    recorded_part(other_user, run, foreign, distance_km=Decimal("9"))
    client.force_login(user)

    assert route(client, run, foreign).status_code == 204


def test_route_endpoint_needs_login(client, run):
    response = route(client, run)

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_route_endpoint_query_budget_does_not_grow_with_history(client, user, run, park):
    """Сколько бы раз ни бегали этот круг — одна выборка."""
    client.force_login(user)

    def queries():
        with CaptureQueriesContext(connection) as context:
            route(client, run, park)
        return len(context.captured_queries)

    recorded_part(user, run, park, distance_km=Decimal("7.4"))
    few = queries()
    for days in range(1, 9):
        recorded_part(user, run, park, days_ago=days, distance_km=Decimal("7.4"))
    assert queries() == few


# ---------- Форма кардио и окно «+ Кардио» ----------


def make_default(place):
    place.is_default = True
    place.save(update_fields=["is_default"])


def test_new_form_from_chooser_is_prefilled_by_default_place(client, user, run, park):
    make_default(park)
    recorded_part(user, run, park, distance_km=Decimal("7.4"), elevation_gain_m=60)
    client.force_login(user)

    content = client.get(reverse("cardio_create"), {"sport": run.pk}).content.decode()

    assert 'value="7.4"' in content
    assert 'value="60"' in content
    assert "Как в прошлый раз в «Парк у реки»" in content
    assert reverse("cardio_route") in content


def test_plan_form_gets_no_route(client, user, run, park):
    """У плана дистанция — цель, а не «как в прошлый раз», и набора высоты нет."""
    make_default(park)
    recorded_part(user, run, park, distance_km=Decimal("7.4"), elevation_gain_m=60)
    client.force_login(user)

    content = client.get(reverse("cardio_prepare"), {"sport": run.pk}).content.decode()

    assert 'value="7.4"' not in content
    assert 'name="elevation_gain_m"' not in content
    assert reverse("cardio_route") not in content


def test_edit_form_shows_elevation_without_prefill(client, user, run, park):
    part = recorded_part(user, run, park, distance_km=Decimal("7.4"), elevation_gain_m=60)
    client.force_login(user)

    content = client.get(reverse("workout_edit", args=[part.workout.pk])).content.decode()

    assert 'value="60"' in content
    assert reverse("cardio_route") not in content


def form_data(sport, **overrides):
    data = {
        "sport": sport.pk,
        "date": timezone.localdate().isoformat(),
        "duration_hours": 0,
        "duration_minutes": 40,
        "distance_km": "7.4",
        "elevation_gain_m": "60",
        "avg_heart_rate": "",
        "note": "",
    }
    return data | overrides


def test_cardio_form_saves_elevation(client, user, run):
    client.force_login(user)

    client.post(reverse("cardio_create"), form_data(run))

    assert CardioPart.objects.get().elevation_gain_m == 60


def test_zero_elevation_is_not_given(client, user, run):
    client.force_login(user)

    client.post(reverse("cardio_create"), form_data(run, elevation_gain_m="0"))

    assert CardioPart.objects.get().elevation_gain_m is None


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("15001", "Набор высоты — до 15000 м."),
        ("-5", "Набор высоты не бывает отрицательным."),
        ("много", "Набор высоты — целое число метров."),
    ],
)
def test_bad_elevation_is_a_form_error(client, user, run, value, error):
    client.force_login(user)

    response = client.post(reverse("cardio_create"), form_data(run, elevation_gain_m=value))

    assert error in response.content.decode()
    assert not Workout.objects.exists()


def test_plan_ignores_posted_elevation(client, user, run):
    """Поля у плана нет — дослать его руками нельзя."""
    client.force_login(user)

    client.post(
        reverse("cardio_prepare"),
        {"sport": run.pk, "distance_km": "10", "elevation_gain_m": "300", "note": ""},
    )

    assert CardioPart.objects.get().elevation_gain_m is None


@pytest.fixture
def strength_workout(user, park):
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    workout = WorkoutFactory(user=user, sport=strength, location=park, duration_min=60)
    StrengthSetFactory(workout=workout)
    return workout


def test_modal_for_new_part_prefills_by_workout_place(client, user, strength_workout):
    client.force_login(user)

    content = client.get(
        reverse("workout_cardio_part", args=[strength_workout.pk])
    ).content.decode()

    assert reverse("cardio_route") in content
    assert 'name="distance_optional"' in content
    assert f'name="location" value="{strength_workout.location_id}"' in content


def test_modal_saves_elevation_and_keeps_it_on_edit(client, user, run, strength_workout):
    client.force_login(user)

    client.post(
        reverse("workout_cardio_part", args=[strength_workout.pk]),
        {
            "sport": run.pk,
            "distance_km": "",
            "elevation_gain_m": "45",
            "duration_hours": 0,
            "duration_minutes": 15,
        },
    )
    part = CardioPart.objects.get()
    content = client.get(
        reverse("workout_cardio_part_edit", args=[strength_workout.pk, part.pk])
    ).content.decode()

    assert part.elevation_gain_m == 45
    assert 'value="45"' in content
    assert reverse("cardio_route") not in content


# ---------- Вывод ----------


def test_part_row_and_history_show_elevation(client, user, run, park, strength_workout):
    CardioPartFactory(workout=strength_workout, sport=run, distance_km=3, elevation_gain_m=85)
    ride = recorded_part(user, run, park, distance_km=Decimal("12.4"), elevation_gain_m=240)
    client.force_login(user)

    summary = client.get(reverse("workout_summary", args=[strength_workout.pk])).content.decode()
    history = client.get(reverse("workout_history")).content.decode()

    assert "набор высоты 85 м" in summary
    assert "набор высоты</span>240 м" in history
    assert f'id="workout-{ride.workout.pk}"' in history
    assert "↑" not in history


# ---------- Корзина ----------


def test_restore_brings_elevation_back(client, user, run, park):
    part = recorded_part(user, run, park, distance_km=Decimal("7.4"), elevation_gain_m=60)
    client.force_login(user)

    client.post(reverse("workout_delete", args=[part.workout.pk]))
    entry = DeletedWorkout.objects.get()
    client.post(reverse("workout_restore", args=[entry.pk]))

    assert CardioPart.objects.get().elevation_gain_m == 60
