"""Действия с подходами живого режима: степперы, отметка, отмена, удаление, отдых."""

from decimal import Decimal

import pytest
from django.urls import reverse

from workouts.models import StrengthSet
from workouts.tests.factories import ExerciseFactory, StrengthSetFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


def planned_set(user, **overrides):
    """Плановый подход активной тренировки пользователя."""
    workout = overrides.pop("workout", None) or WorkoutFactory(user=user, duration_min=None)
    defaults = {"workout": workout, "set_number": 1, "done": False}
    return StrengthSetFactory(**defaults | overrides)


def adjust(client, row, field, direction):
    return client.post(reverse("set_adjust", args=[row.pk]), {"field": field, "dir": direction})


def test_weight_stepper_changes_weight(client, user):
    client.force_login(user)
    row = planned_set(user, weight_kg=80)

    response = adjust(client, row, "weight_kg", "up")

    row.refresh_from_db()
    assert row.weight_kg == Decimal("82.5")
    assert response.content.decode() == "82,5"


def test_reps_stepper_changes_reps(client, user):
    client.force_login(user)
    row = planned_set(user, reps=8)

    response = adjust(client, row, "reps", "down")

    row.refresh_from_db()
    assert row.reps == 7
    assert response.content.decode() == "7"


def test_weight_does_not_go_below_zero(client, user):
    client.force_login(user)
    row = planned_set(user, weight_kg=0)

    adjust(client, row, "weight_kg", "down")

    row.refresh_from_db()
    assert row.weight_kg == 0


def test_weight_clamps_at_upper_limit(client, user):
    """Выше max_digits=5 подняться нельзя — иначе save() упал бы с 500."""
    client.force_login(user)
    row = planned_set(user, weight_kg=Decimal("999.99"))

    response = adjust(client, row, "weight_kg", "up")

    row.refresh_from_db()
    assert response.status_code == 200
    assert row.weight_kg == Decimal("999.99")


@pytest.mark.parametrize(
    ("field", "direction"),
    [("weight_kg", "sideways"), ("height", "up"), ("", "")],
)
def test_stepper_rejects_unknown_field_or_direction(client, user, field, direction):
    client.force_login(user)
    row = planned_set(user)

    response = adjust(client, row, field, direction)

    assert response.status_code == 400


def test_done_set_cannot_be_adjusted(client, user):
    client.force_login(user)
    row = planned_set(user, done=True)

    response = adjust(client, row, "weight_kg", "up")

    assert response.status_code == 404


def test_sets_of_finished_workout_are_immutable(client, user):
    client.force_login(user)
    finished = WorkoutFactory(user=user)
    row = StrengthSetFactory(workout=finished, set_number=1, done=False)

    response = adjust(client, row, "weight_kg", "up")

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("url_name", "payload"),
    [
        pytest.param("set_adjust", {"field": "weight_kg", "dir": "up"}, id="adjust"),
        pytest.param("set_done", {}, id="done"),
        pytest.param("set_undo", {}, id="undo"),
        pytest.param("set_delete", {}, id="delete"),
    ],
)
def test_foreign_set_is_not_reachable(client, user, other_user, url_name, payload):
    client.force_login(user)
    alien_workout = WorkoutFactory(user=other_user, duration_min=None)
    row = StrengthSetFactory(workout=alien_workout, set_number=1, done=False)

    response = client.post(reverse(url_name, args=[row.pk]), payload)

    assert response.status_code == 404
    assert StrengthSet.objects.filter(pk=row.pk, done=False).exists()


def test_set_done_marks_done_and_starts_timer(client, user):
    client.force_login(user)
    row = planned_set(user, weight_kg=80, reps=8)

    response = client.post(reverse("set_done", args=[row.pk]))

    row.refresh_from_db()
    content = response.content.decode()
    assert row.done
    assert 'hx-swap-oob="true"' in content
    assert 'id="rest-card"' in content
    assert "data-autostart" in content


def test_set_done_requires_at_least_one_rep(client, user):
    client.force_login(user)
    row = planned_set(user, reps=0)

    response = client.post(reverse("set_done", args=[row.pk]))

    row.refresh_from_db()
    assert response.status_code == 200
    assert not row.done
    assert "Укажите повторения" in response.content.decode()


def test_set_done_twice_is_idempotent_and_does_not_restart_timer(client, user):
    client.force_login(user)
    row = planned_set(user, reps=8)

    client.post(reverse("set_done", args=[row.pk]))
    second = client.post(reverse("set_done", args=[row.pk]))

    row.refresh_from_db()
    assert row.done
    assert "data-autostart" not in second.content.decode()


def test_add_set_appends_next_number_and_copies_previous(client, user):
    client.force_login(user)
    workout = WorkoutFactory(user=user, duration_min=None)
    exercise = ExerciseFactory()
    StrengthSetFactory(
        workout=workout, exercise=exercise, set_number=1, weight_kg=80, reps=8, done=True
    )
    StrengthSetFactory(
        workout=workout, exercise=exercise, set_number=2, weight_kg=75, reps=10, done=False
    )

    client.post(reverse("live_set_add", args=[workout.pk]), {"exercise": exercise.pk})

    added = workout.sets.get(set_number=3)
    assert added.weight_kg == 75
    assert added.reps == 10
    assert not added.done


def test_add_set_for_exercise_not_in_workout_is_rejected(client, user):
    client.force_login(user)
    workout = WorkoutFactory(user=user, duration_min=None)
    stranger = ExerciseFactory()

    response = client.post(reverse("live_set_add", args=[workout.pk]), {"exercise": stranger.pk})

    assert response.status_code == 404
    assert not workout.sets.exists()


def test_undone_set_can_be_deleted_and_done_cannot(client, user):
    client.force_login(user)
    workout = WorkoutFactory(user=user, duration_min=None)
    exercise = ExerciseFactory()
    done_row = StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=True)
    planned_row = StrengthSetFactory(workout=workout, exercise=exercise, set_number=2, done=False)

    assert client.post(reverse("set_delete", args=[planned_row.pk])).status_code == 200
    assert client.post(reverse("set_delete", args=[done_row.pk])).status_code == 404

    assert not StrengthSet.objects.filter(pk=planned_row.pk).exists()
    assert StrengthSet.objects.filter(pk=done_row.pk).exists()


def test_undo_returns_set_to_editable_and_makes_exercise_current(client, user):
    client.force_login(user)
    workout = WorkoutFactory(user=user, duration_min=None)
    bench = ExerciseFactory(name="Жим лёжа")
    squat = ExerciseFactory(name="Присед")
    done_row = StrengthSetFactory(
        workout=workout, exercise=bench, set_number=1, weight_kg=80, reps=8, done=True
    )
    StrengthSetFactory(workout=workout, exercise=squat, set_number=1, done=False)

    response = client.post(reverse("set_undo", args=[done_row.pk]))

    done_row.refresh_from_db()
    workout.refresh_from_db()
    assert not done_row.done
    assert done_row.weight_kg == 80
    assert workout.current_exercise == bench
    assert "data-autostart" not in response.content.decode()


def test_rest_duration_is_saved_and_clamped(client, user):
    client.force_login(user)
    workout = WorkoutFactory(user=user, duration_min=None)
    url = reverse("live_rest", args=[workout.pk])

    response = client.post(url, {"delta": "-15"})
    workout.refresh_from_db()
    assert response.status_code == 204
    assert workout.rest_seconds == 75  # дефолт профиля 90 − 15

    workout.rest_seconds = 15
    workout.save(update_fields=["rest_seconds"])
    client.post(url, {"delta": "-15"})
    workout.refresh_from_db()
    assert workout.rest_seconds == 15  # ниже нижней границы не уходит


def test_rest_rejects_unknown_delta(client, user):
    client.force_login(user)
    workout = WorkoutFactory(user=user, duration_min=None)

    response = client.post(reverse("live_rest", args=[workout.pk]), {"delta": "999"})

    assert response.status_code == 400


def test_effective_rest_falls_back_to_profile_default(user):
    user.rest_seconds_default = 120
    workout = WorkoutFactory(user=user, duration_min=None)

    assert workout.effective_rest_seconds == 120

    # 0 — валидное «без отдыха», а не «возьми дефолт».
    workout.rest_seconds = 0
    assert workout.effective_rest_seconds == 0
