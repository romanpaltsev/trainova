"""Ввод, правка и удаление кардио-тренировки."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.models import CardioDetails, Sport, Workout
from workouts.tests.factories import CardioDetailsFactory, SportFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def bike(db):
    return SportFactory(name="Велосипед", category=Sport.Category.CARDIO, owner=None)


def form_data(sport=None, **overrides):
    data = {
        "sport": sport.pk if sport else "",
        "date": timezone.localdate().isoformat(),
        "duration_hours": 1,
        "duration_minutes": 24,
        "distance_km": "32.4",
        "avg_heart_rate": 142,
        "note": "Круг вдоль набережной",
    }
    return data | overrides


def test_cardio_workout_is_created(client, user, bike):
    client.force_login(user)

    response = client.post(reverse("cardio_create"), form_data(bike))

    assert response.status_code == 302
    workout = Workout.objects.get(user=user)
    assert workout.sport == bike
    assert workout.duration_min == 84
    assert workout.note == "Круг вдоль набережной"
    assert workout.cardio.distance_km == Decimal("32.40")
    assert workout.cardio.avg_heart_rate == 142
    assert workout.cardio.metric_display == "23,1 км/ч"


def test_cardio_workout_defaults_to_today(client, user, bike):
    client.force_login(user)

    response = client.get(reverse("cardio_create"))

    assert response.context["form"].initial["date"] == timezone.localdate()


def test_past_date_gets_noon_and_today_gets_current_time(client, user, bike):
    client.force_login(user)
    yesterday = timezone.localdate() - timedelta(days=1)

    client.post(reverse("cardio_create"), form_data(bike, date=yesterday.isoformat()))
    client.post(reverse("cardio_create"), form_data(bike))

    past, today = Workout.objects.order_by("started_at")
    assert timezone.localtime(past.started_at).hour == 12
    assert timezone.localtime(today.started_at).date() == timezone.localdate()


def test_sport_choices_exclude_other_users_and_strength(client, user, bike, other_user):
    client.force_login(user)
    own = SportFactory(name="Гребля", category=Sport.Category.CARDIO, owner=user)
    alien = SportFactory(name="Каяк", category=Sport.Category.CARDIO, owner=other_user)
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH, owner=None)

    choices = client.get(reverse("cardio_create")).context["form"].fields["sport"].queryset

    assert set(choices) == {bike, own}
    assert alien not in choices
    assert strength not in choices


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"duration_hours": 0, "duration_minutes": 0}, "duration_minutes"),
        ({"duration_hours": 25}, "duration_hours"),
        ({"distance_km": "0"}, "distance_km"),
        ({"distance_km": ""}, "distance_km"),
        ({"avg_heart_rate": 300}, "avg_heart_rate"),
        ({"sport": ""}, "sport"),  # sport передаётся через overrides
    ],
)
def test_invalid_input_is_rejected(client, user, bike, overrides, field):
    client.force_login(user)

    data = form_data(bike)
    data.update(overrides)

    response = client.post(reverse("cardio_create"), data)

    assert response.status_code == 200
    assert field in response.context["form"].errors
    assert not Workout.objects.exists()


def test_future_date_is_rejected(client, user, bike):
    client.force_login(user)
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()

    response = client.post(reverse("cardio_create"), form_data(bike, date=tomorrow))

    assert "будущем" in str(response.context["form"].errors["date"])
    assert not Workout.objects.exists()


def test_other_users_sport_cannot_be_used(client, user, other_user):
    client.force_login(user)
    alien = SportFactory(name="Каяк", category=Sport.Category.CARDIO, owner=other_user)

    response = client.post(reverse("cardio_create"), form_data(alien))

    assert "sport" in response.context["form"].errors
    assert not Workout.objects.exists()


def test_workout_can_be_edited(client, user, bike):
    client.force_login(user)
    cardio = CardioDetailsFactory(workout__user=user, workout__sport=bike, distance_km=10)
    workout = cardio.workout

    response = client.post(
        reverse("workout_edit", args=[workout.pk]),
        form_data(bike, distance_km="42.2", duration_hours=2, duration_minutes=0),
    )

    assert response.status_code == 302
    workout.refresh_from_db()
    workout.cardio.refresh_from_db()
    assert workout.duration_min == 120
    assert workout.cardio.distance_km == Decimal("42.20")
    assert CardioDetails.objects.count() == 1


def test_edit_form_is_prefilled(client, user, bike):
    client.force_login(user)
    cardio = CardioDetailsFactory(
        workout__user=user, workout__sport=bike, distance_km=25, avg_heart_rate=131
    )
    cardio.workout.duration_min = 95
    cardio.workout.save()

    initial = client.get(reverse("workout_edit", args=[cardio.workout.pk])).context["form"].initial

    assert initial["duration_hours"] == 1
    assert initial["duration_minutes"] == 35
    assert initial["distance_km"] == Decimal("25.00")
    assert initial["avg_heart_rate"] == 131


def test_workout_is_deleted_after_confirmation(client, user, bike):
    client.force_login(user)
    workout = CardioDetailsFactory(workout__user=user, workout__sport=bike).workout

    confirm = client.get(reverse("workout_delete", args=[workout.pk]))
    assert confirm.status_code == 200
    assert Workout.objects.count() == 1

    response = client.post(reverse("workout_delete", args=[workout.pk]))

    assert response.status_code == 302
    assert not Workout.objects.exists()
    assert not CardioDetails.objects.exists()


@pytest.mark.parametrize("url_name", ["workout_edit", "workout_delete"])
def test_other_users_workout_is_not_reachable(client, user, other_user, url_name):
    client.force_login(user)
    alien = WorkoutFactory(user=other_user, sport__category=Sport.Category.CARDIO)

    assert client.get(reverse(url_name, args=[alien.pk])).status_code == 404
    assert client.post(reverse(url_name, args=[alien.pk]), {}).status_code == 404
    assert Workout.objects.filter(pk=alien.pk).exists()


def test_strength_workout_has_no_cardio_edit_screen(client, user):
    client.force_login(user)
    strength = WorkoutFactory(user=user, sport__category=Sport.Category.STRENGTH)

    assert client.get(reverse("workout_edit", args=[strength.pk])).status_code == 404


def test_anonymous_is_redirected_to_login(client, bike):
    for url in [reverse("cardio_create"), reverse("workout_history")]:
        response = client.get(url)

        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]


def test_date_is_rendered_in_iso_for_native_date_input(client, user, bike):
    """<input type="date"> понимает только ISO; с локалью ru-ru легко получить 27.08.2026."""
    client.force_login(user)

    html = client.get(reverse("cardio_create")).content.decode()

    assert f'value="{timezone.localdate().isoformat()}"' in html


def test_iso_date_from_browser_is_accepted(client, user, bike):
    client.force_login(user)
    yesterday = timezone.localdate() - timedelta(days=1)

    response = client.post(reverse("cardio_create"), form_data(bike, date=yesterday.isoformat()))

    assert response.status_code == 302
    assert timezone.localtime(Workout.objects.get(user=user).started_at).date() == yesterday
