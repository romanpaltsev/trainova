"""Старт живого режима и экран активной силовой тренировки."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from workouts import services
from workouts.models import Sport, Workout
from workouts.tests.factories import (
    ExerciseFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def start_strength(client, sport):
    return client.post(reverse("strength_start"), {"sport": sport.pk})


def test_start_creates_active_workout_and_redirects_to_live(client, user):
    client.force_login(user)
    sport = SportFactory()

    response = start_strength(client, sport)

    workout = Workout.objects.get(user=user)
    assert workout.duration_min is None
    assert workout.sport == sport
    assert response.status_code == 302
    assert response.url == reverse("workout_live", args=[workout.pk])


def test_start_modal_lists_only_visible_strength_sports(client, user, other_user):
    client.force_login(user)
    SportFactory(owner=user, name="Кроссфит")
    SportFactory(owner=other_user, name="Чужой спорт")
    SportFactory(category=Sport.Category.CARDIO, name="Велосипед")
    SportFactory(name="Силовая")

    content = client.get(reverse("workout_start")).content.decode()

    assert "Кроссфит" in content
    assert "Силовая" in content
    assert "Чужой спорт" not in content
    # Кардио стартует не отсюда, а через свою форму.
    assert "Велосипед" not in content


def test_start_of_foreign_or_cardio_sport_is_404(client, user, other_user):
    client.force_login(user)

    assert start_strength(client, SportFactory(owner=other_user)).status_code == 404
    assert start_strength(client, SportFactory(category=Sport.Category.CARDIO)).status_code == 404


def test_start_modal_offers_to_continue_active_workout(client, user):
    client.force_login(user)
    active = WorkoutFactory(user=user, duration_min=None)

    content = client.get(reverse("workout_start")).content.decode()

    assert "Продолжить тренировку" in content
    assert reverse("workout_live", args=[active.pk]) in content
    assert reverse("strength_start") not in content


def test_second_active_workout_is_impossible(user):
    """Гонку двух вкладок ловит частичный уникальный индекс, а не код."""
    WorkoutFactory(user=user, duration_min=None)

    with pytest.raises(IntegrityError), transaction.atomic():
        WorkoutFactory(user=user, duration_min=None)


def test_start_redirects_to_existing_active_workout(client, user):
    client.force_login(user)
    active = WorkoutFactory(user=user, duration_min=None)

    response = start_strength(client, SportFactory())

    assert response.status_code == 302
    assert response.url == reverse("workout_live", args=[active.pk])
    assert Workout.objects.filter(user=user).count() == 1


def test_live_screen_shows_planned_sets_and_queue_hints(client, user):
    client.force_login(user)
    bench = ExerciseFactory(name="Жим лёжа")
    squat = ExerciseFactory(name="Присед со штангой")
    past = WorkoutFactory(user=user)
    StrengthSetFactory(workout=past, exercise=bench, set_number=1, weight_kg=70, reps=10)
    StrengthSetFactory(
        workout=past, exercise=bench, set_number=2, weight_kg=Decimal("77.5"), reps=8
    )
    StrengthSetFactory(workout=past, exercise=squat, set_number=1, weight_kg=100, reps=5)
    active = WorkoutFactory(user=user, duration_min=None)
    services.create_planned_sets(active, bench)
    services.create_planned_sets(active, squat)

    content = client.get(reverse("workout_live", args=[active.pk])).content.decode()

    assert "Жим лёжа" in content
    assert "прошлый раз: 70×10 · 77,5×8" in content
    assert "Дальше" in content
    assert "Присед со штангой" in content
    assert "1 подход · до 100 кг" in content


def test_live_screen_of_other_user_is_404(client, user, other_user):
    client.force_login(user)
    alien = WorkoutFactory(user=other_user, duration_min=None)

    response = client.get(reverse("workout_live", args=[alien.pk]))

    assert response.status_code == 404


def test_live_screen_of_finished_workout_redirects_to_summary(client, user):
    client.force_login(user)
    workout = WorkoutFactory(user=user)

    response = client.get(reverse("workout_live", args=[workout.pk]))

    assert response.status_code == 302
    assert response.url == reverse("workout_summary", args=[workout.pk])


def test_live_screen_for_cardio_is_404(client, user):
    client.force_login(user)
    cardio = WorkoutFactory(user=user, sport__category=Sport.Category.CARDIO)

    response = client.get(reverse("workout_live", args=[cardio.pk]))

    assert response.status_code == 404


def test_history_hides_active_workout(client, user):
    client.force_login(user)
    finished = WorkoutFactory(user=user)
    active = WorkoutFactory(user=user, duration_min=None)

    workouts = list(client.get(reverse("workout_history")).context["workouts"])

    assert finished in workouts
    assert active not in workouts


@pytest.mark.parametrize(
    ("url_name", "payload"),
    [
        pytest.param("live_rest", {"delta": "15"}, id="rest"),
        pytest.param("live_set_add", {"exercise": "1"}, id="add-set"),
        pytest.param("live_exercise_select", {"exercise": "1"}, id="select"),
        pytest.param("live_exercises", {"name": "Жим лёжа"}, id="attach"),
        pytest.param("workout_finish", {}, id="finish"),
    ],
)
def test_foreign_workout_actions_are_404(client, user, other_user, url_name, payload):
    client.force_login(user)
    alien = WorkoutFactory(user=other_user, duration_min=None)

    response = client.post(reverse(url_name, args=[alien.pk]), payload)

    assert response.status_code == 404


def test_anonymous_is_redirected_to_login(client):
    response = client.get(reverse("workout_start"))

    assert response.status_code == 302
    assert reverse("account_login") in response.url
