"""Место в тренировке: силовая берёт дефолт молча, кардио спрашивает."""

import pytest
from django.urls import reverse

from workouts.models import Location, Sport, Workout
from workouts.tests.factories import (
    CardioDetailsFactory,
    LocationFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def strength(user):
    return SportFactory(name="Силовая", category=Sport.Category.STRENGTH)


@pytest.fixture
def bike(user):
    return SportFactory(name="Велосипед", category=Sport.Category.CARDIO)


def cardio_data(sport, **overrides):
    data = {
        "sport": sport.pk,
        "date": "2026-09-01",
        "duration_hours": "",
        "duration_minutes": "45",
        "distance_km": "20",
        "avg_heart_rate": "",
        "note": "",
        "location": "",
        "location_own": "",
    }
    return data | overrides


# ---------- Силовая ----------


@pytest.mark.parametrize(
    ("url_name", "planned"),
    [
        pytest.param("strength_start", False, id="now"),
        pytest.param("strength_prepare", True, id="prepare"),
    ],
)
def test_strength_takes_the_default_location(client, user, strength, url_name, planned):
    place = LocationFactory(owner=user, name="СпортЛайф", is_default=True)

    client.force_login(user)
    client.post(reverse(url_name), {"sport": strength.pk})

    workout = Workout.objects.get(user=user)
    assert workout.location == place
    assert workout.is_planned is planned


def test_strength_start_without_default_leaves_location_empty(client, user, strength):
    """Место не выбрано — тренировка пишется без него, а не падает."""
    LocationFactory(owner=user, name="СпортЛайф")

    client.force_login(user)
    client.post(reverse("strength_start"), {"sport": strength.pk})

    assert Workout.objects.get(user=user).location is None


def test_strength_start_ignores_other_users_default(client, user, other_user, strength):
    """Изоляция: чужой дефолт не попадает в мою тренировку."""
    LocationFactory(owner=other_user, name="Чужой зал", is_default=True)

    client.force_login(user)
    client.post(reverse("strength_start"), {"sport": strength.pk})

    assert Workout.objects.get(user=user).location is None


def test_repeat_takes_current_default_not_source_location(client, user, strength):
    """«Повторить» — это «сделать то же сегодня», а не «скопировать запись»."""
    old = LocationFactory(owner=user, name="Зал в командировке")
    current = LocationFactory(owner=user, name="СпортЛайф", is_default=True)
    source = WorkoutFactory(user=user, sport=strength, location=old)
    StrengthSetFactory(workout=source, set_number=1)

    client.force_login(user)
    client.post(reverse("workout_repeat", args=[source.pk]))

    fresh = Workout.objects.filter(user=user).live().get()
    assert fresh.location == current


# ---------- Кардио ----------


def test_cardio_form_preselects_the_default_location(client, user, bike):
    place = LocationFactory(owner=user, name="СпортЛайф", is_default=True)

    client.force_login(user)
    response = client.get(reverse("cardio_create"))

    assert response.context["form"]["location"].value() == place.pk


def test_cardio_form_saves_the_chosen_location(client, user, bike):
    place = LocationFactory(owner=user, name="Парк у реки")

    client.force_login(user)
    client.post(reverse("cardio_create"), cardio_data(bike, location=place.pk))

    assert Workout.objects.get(user=user).location == place


def test_cardio_can_be_saved_without_location(client, user, bike):
    client.force_login(user)
    response = client.post(reverse("cardio_create"), cardio_data(bike))

    assert response.status_code == 302
    assert Workout.objects.get(user=user).location is None


def test_cardio_form_creates_location_from_own_field(client, user, bike):
    client.force_login(user)
    client.post(reverse("cardio_create"), cardio_data(bike, location_own="Парк Кузьминки"))

    place = Location.objects.get(owner=user)
    assert place.name == "Парк Кузьминки"
    assert Workout.objects.get(user=user).location == place


def test_cardio_own_field_wins_over_the_chip(client, user, bike):
    """Правило chosen_muscle_group: своё поле перебивает выбранный чип."""
    chip = LocationFactory(owner=user, name="СпортЛайф")

    client.force_login(user)
    client.post(
        reverse("cardio_create"),
        cardio_data(bike, location=chip.pk, location_own="Парк у реки"),
    )

    assert Workout.objects.get(user=user).location.name == "Парк у реки"


def test_cardio_own_field_reuses_existing_location(client, user, bike):
    """Совпадение названия выбирает существующее место, а не создаёт дубль."""
    place = LocationFactory(owner=user, name="СпортЛайф")

    client.force_login(user)
    client.post(reverse("cardio_create"), cardio_data(bike, location_own="спортлайф"))

    assert Workout.objects.get(user=user).location == place
    assert Location.objects.filter(owner=user).count() == 1


def test_cardio_form_rejects_foreign_location(client, user, other_user, bike):
    """Чужое место по id — ошибка формы, тренировка не записана."""
    theirs = LocationFactory(owner=other_user, name="Чужой зал")

    client.force_login(user)
    response = client.post(reverse("cardio_create"), cardio_data(bike, location=theirs.pk))

    assert response.status_code == 200
    assert response.context["form"].errors["location"]
    assert not Workout.objects.filter(user=user).exists()


def test_cardio_form_shows_only_own_locations(client, user, other_user, bike):
    mine = LocationFactory(owner=user, name="СпортЛайф")
    LocationFactory(owner=other_user, name="Чужой зал")

    client.force_login(user)
    response = client.get(reverse("cardio_create"))

    content = response.content.decode()
    assert list(response.context["locations"]) == [mine]
    assert "Чужой зал" not in content


def test_cardio_edit_keeps_the_location(client, user, bike):
    """Правка без трогания места не должна его стирать."""
    place = LocationFactory(owner=user, name="Парк у реки")
    workout = CardioDetailsFactory(workout__user=user, workout__sport=bike).workout
    workout.location = place
    workout.save(update_fields=["location"])

    client.force_login(user)
    response = client.get(reverse("workout_edit", args=[workout.pk]))
    client.post(reverse("workout_edit", args=[workout.pk]), cardio_data(bike, location=place.pk))

    workout.refresh_from_db()
    assert response.context["form"]["location"].value() == place.pk
    assert workout.location == place


def test_cardio_edit_does_not_substitute_the_default(client, user, bike):
    """Подстановка дефолта на правке подменила бы место записанной тренировки."""
    LocationFactory(owner=user, name="СпортЛайф", is_default=True)
    workout = CardioDetailsFactory(workout__user=user, workout__sport=bike).workout

    client.force_login(user)
    response = client.get(reverse("workout_edit", args=[workout.pk]))

    assert response.context["form"]["location"].value() is None


def test_cardio_location_can_be_cleared_on_edit(client, user, bike):
    place = LocationFactory(owner=user, name="Парк у реки")
    workout = CardioDetailsFactory(workout__user=user, workout__sport=bike).workout
    workout.location = place
    workout.save(update_fields=["location"])

    client.force_login(user)
    client.post(reverse("workout_edit", args=[workout.pk]), cardio_data(bike))

    workout.refresh_from_db()
    assert workout.location is None


# ---------- Смена места на экране тренировки ----------


def live(user, sport, **overrides):
    return WorkoutFactory(user=user, sport=sport, duration_min=None, **overrides)


def test_location_modal_lists_only_my_places(client, user, other_user, strength):
    mine = LocationFactory(owner=user, name="СпортЛайф")
    LocationFactory(owner=other_user, name="Чужой зал")
    workout = live(user, strength)

    client.force_login(user)
    response = client.get(reverse("workout_location", args=[workout.pk]))

    content = response.content.decode()
    assert list(response.context["locations"]) == [mine]
    assert "Чужой зал" not in content
    assert "Без места" in content


def test_location_can_be_changed(client, user, strength):
    old = LocationFactory(owner=user, name="СпортЛайф", is_default=True)
    new = LocationFactory(owner=user, name="Дома")
    workout = live(user, strength, location=old)

    client.force_login(user)
    client.post(reverse("workout_location", args=[workout.pk]), {"location": new.pk})

    workout.refresh_from_db()
    assert workout.location == new


def test_location_response_updates_the_head_out_of_band(client, user, strength):
    place = LocationFactory(owner=user, name="СпортЛайф")
    workout = live(user, strength)

    client.force_login(user)
    content = client.post(
        reverse("workout_location", args=[workout.pk]), {"location": place.pk}
    ).content.decode()

    assert 'hx-swap-oob="true"' in content
    assert 'id="workout-location"' in content
    assert "СпортЛайф" in content
    # Пустой остаток ответа закрывает модалку: её разметки в ответе быть не должно.
    assert "Место тренировки" not in content


def test_draft_location_can_be_changed(client, user, strength):
    """Черновик правится теми же эндпоинтами, что и идущая тренировка."""
    place = LocationFactory(owner=user, name="СпортЛайф")
    draft = WorkoutFactory(user=user, sport=strength, started_at=None, duration_min=None)

    client.force_login(user)
    client.post(reverse("workout_location", args=[draft.pk]), {"location": place.pk})

    draft.refresh_from_db()
    assert draft.location == place


def test_finished_strength_location_can_be_fixed(client, user, strength):
    """Экрана правки силовой нет, поэтому забытое место чинится здесь."""
    place = LocationFactory(owner=user, name="СпортЛайф")
    workout = WorkoutFactory(user=user, sport=strength)

    client.force_login(user)
    client.post(reverse("workout_location", args=[workout.pk]), {"location": place.pk})

    workout.refresh_from_db()
    assert workout.location == place


def test_location_can_be_cleared(client, user, strength):
    place = LocationFactory(owner=user, name="СпортЛайф")
    workout = live(user, strength, location=place)

    client.force_login(user)
    client.post(reverse("workout_location", args=[workout.pk]), {"location": ""})

    workout.refresh_from_db()
    assert workout.location is None


def test_new_place_can_be_created_on_the_workout_screen(client, user, strength):
    workout = live(user, strength)

    client.force_login(user)
    client.post(reverse("workout_location", args=[workout.pk]), {"location_own": "Зал у дома"})

    workout.refresh_from_db()
    assert workout.location.name == "Зал у дома"
    assert workout.location.owner == user
    # Первое место сразу становится дефолтным — иначе подстановка молчала бы.
    assert workout.location.is_default


def test_typed_name_reuses_existing_place(client, user, strength):
    place = LocationFactory(owner=user, name="СпортЛайф")
    workout = live(user, strength)

    client.force_login(user)
    client.post(reverse("workout_location", args=[workout.pk]), {"location_own": "спортлайф"})

    workout.refresh_from_db()
    assert workout.location == place
    assert Location.objects.filter(owner=user).count() == 1


def test_typed_name_wins_over_the_selected_row(client, user, strength):
    chip = LocationFactory(owner=user, name="СпортЛайф")
    workout = live(user, strength)

    client.force_login(user)
    client.post(
        reverse("workout_location", args=[workout.pk]),
        {"location": chip.pk, "location_own": "Дома"},
    )

    workout.refresh_from_db()
    assert workout.location.name == "Дома"


def test_foreign_location_cannot_be_attached(client, user, other_user, strength):
    theirs = LocationFactory(owner=other_user, name="Чужой зал")
    workout = live(user, strength)

    client.force_login(user)
    response = client.post(reverse("workout_location", args=[workout.pk]), {"location": theirs.pk})

    assert response.status_code == 404
    workout.refresh_from_db()
    assert workout.location is None


@pytest.mark.parametrize("method", ["get", "post"])
def test_foreign_workout_location_is_404(client, user, other_user, strength, method):
    theirs = WorkoutFactory(user=other_user, sport=strength)
    place = LocationFactory(owner=user, name="СпортЛайф")

    client.force_login(user)
    response = getattr(client, method)(
        reverse("workout_location", args=[theirs.pk]), {"location": place.pk}
    )

    assert response.status_code == 404
    theirs.refresh_from_db()
    assert theirs.location is None


@pytest.mark.parametrize("method", ["get", "post"])
def test_anonymous_cannot_change_location(client, user, strength, method):
    place = LocationFactory(owner=user, name="СпортЛайф")
    workout = live(user, strength)

    response = getattr(client, method)(
        reverse("workout_location", args=[workout.pk]), {"location": place.pk}
    )

    assert response.status_code == 302
    workout.refresh_from_db()
    assert workout.location is None


def test_live_screen_shows_the_location_control(client, user, strength):
    place = LocationFactory(owner=user, name="СпортЛайф")
    workout = live(user, strength, location=place)
    StrengthSetFactory(workout=workout, set_number=1, done=False)

    client.force_login(user)
    content = client.get(reverse("workout_live", args=[workout.pk])).content.decode()

    assert 'id="workout-location"' in content
    assert "СпортЛайф" in content


def test_summary_shows_the_location_control(client, user, strength):
    workout = WorkoutFactory(user=user, sport=strength)
    StrengthSetFactory(workout=workout, set_number=1)

    client.force_login(user)
    content = client.get(reverse("workout_summary", args=[workout.pk])).content.decode()

    # Места нет — кнопка всё равно есть: это точка входа в фичу.
    assert 'id="workout-location"' in content
    assert "Указать место" in content
