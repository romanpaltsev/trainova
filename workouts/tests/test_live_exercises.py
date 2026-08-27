"""Модалка «+ Упражнение»: поиск, добавление, быстрое создание, переключение очереди."""

import pytest
from django.urls import reverse

from workouts import services
from workouts.models import Exercise
from workouts.tests.factories import ExerciseFactory, StrengthSetFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def active(user):
    return WorkoutFactory(user=user, duration_min=None)


def modal_url(workout):
    return reverse("live_exercises", args=[workout.pk])


def test_search_shows_visible_exercises_only(client, user, other_user, active):
    client.force_login(user)
    ExerciseFactory(name="Жим лёжа")
    ExerciseFactory(owner=user, name="Моё упражнение")
    ExerciseFactory(owner=other_user, name="Чужое упражнение")

    content = client.get(modal_url(active)).content.decode()

    assert "Жим лёжа" in content
    assert "Моё упражнение" in content
    assert "Чужое упражнение" not in content


def test_search_filters_case_insensitive(client, user, active):
    client.force_login(user)
    ExerciseFactory(name="Жим лёжа")
    ExerciseFactory(name="Присед со штангой")

    content = client.get(modal_url(active), {"q": "жим"}).content.decode()

    assert "Жим лёжа" in content
    assert "Присед со штангой" not in content


def test_already_added_exercises_are_hidden_from_results(client, user, active):
    client.force_login(user)
    bench = ExerciseFactory(name="Жим лёжа")
    services.create_planned_sets(active, bench)

    content = client.get(modal_url(active), {"q": "Жим"}).content.decode()

    assert "Жим лёжа" not in content


def test_add_exercise_prefills_sets_from_last_workout(client, user, active):
    client.force_login(user)
    bench = ExerciseFactory(name="Жим лёжа")
    past = WorkoutFactory(user=user)
    StrengthSetFactory(workout=past, exercise=bench, set_number=1, weight_kg=70, reps=10)
    StrengthSetFactory(workout=past, exercise=bench, set_number=2, weight_kg=80, reps=5)

    response = client.post(modal_url(active), {"exercise": bench.pk})

    rows = list(active.sets.order_by("set_number"))
    assert [(row.weight_kg, row.reps, row.done) for row in rows] == [
        (70, 10, False),
        (80, 5, False),
    ]
    # Модалка закрывается пустым телом, регион упражнений приходит out-of-band.
    assert 'hx-swap-oob="true"' in response.content.decode()


def test_add_exercise_without_history_creates_one_empty_set(client, user, active):
    client.force_login(user)
    bench = ExerciseFactory()

    client.post(modal_url(active), {"exercise": bench.pk})

    rows = list(active.sets.all())
    assert [(row.weight_kg, row.reps) for row in rows] == [(0, 0)]


def test_add_same_exercise_twice_is_noop(client, user, active):
    client.force_login(user)
    bench = ExerciseFactory()

    client.post(modal_url(active), {"exercise": bench.pk})
    response = client.post(modal_url(active), {"exercise": bench.pk})

    assert response.status_code == 200
    assert active.sets.count() == 1


def test_new_exercise_is_created_as_personal(client, user, active):
    client.force_login(user)

    client.post(modal_url(active), {"name": "Тяга к поясу"})

    exercise = Exercise.objects.get(name="Тяга к поясу")
    assert exercise.owner == user
    assert active.sets.filter(exercise=exercise).count() == 1


def test_existing_name_is_reused_instead_of_duplicated(client, user, active):
    client.force_login(user)
    bench = ExerciseFactory(name="Жим лёжа")

    client.post(modal_url(active), {"name": "жим лёжа"})

    assert Exercise.objects.count() == 1
    assert active.sets.filter(exercise=bench).exists()


def test_foreign_exercise_id_is_404(client, user, other_user, active):
    client.force_login(user)
    alien = ExerciseFactory(owner=other_user)

    response = client.post(modal_url(active), {"exercise": alien.pk})

    assert response.status_code == 404
    assert not active.sets.exists()


def test_create_offer_appears_only_without_exact_match(client, user, active):
    client.force_login(user)
    ExerciseFactory(name="Жим лёжа")

    with_offer = client.get(modal_url(active), {"q": "Жим лёжа в наклоне"}).content.decode()
    without_offer = client.get(modal_url(active), {"q": "Жим лёжа"}).content.decode()

    assert "Создать" in with_offer
    assert "Создать" not in without_offer


def test_modal_requires_own_active_workout(client, user, other_user):
    client.force_login(user)
    alien = WorkoutFactory(user=other_user, duration_min=None)
    finished = WorkoutFactory(user=user)

    assert client.get(modal_url(alien)).status_code == 404
    assert client.get(modal_url(finished)).status_code == 404


def test_queue_tap_switches_current_exercise(client, user, active):
    client.force_login(user)
    bench = ExerciseFactory(name="Жим лёжа")
    squat = ExerciseFactory(name="Присед")
    services.create_planned_sets(active, bench)
    services.create_planned_sets(active, squat)

    response = client.post(
        reverse("live_exercise_select", args=[active.pk]), {"exercise": squat.pk}
    )

    active.refresh_from_db()
    assert response.status_code == 200
    assert active.current_exercise == squat
    assert services.live_context(active)["current_group"]["exercise"] == squat


def test_select_exercise_not_in_workout_is_rejected(client, user, active):
    client.force_login(user)
    stranger = ExerciseFactory()

    response = client.post(
        reverse("live_exercise_select", args=[active.pk]), {"exercise": stranger.pk}
    )

    assert response.status_code == 404


def test_current_falls_back_when_selected_exercise_is_done(client, user, active):
    client.force_login(user)
    bench = ExerciseFactory(name="Жим лёжа")
    squat = ExerciseFactory(name="Присед")
    services.create_planned_sets(active, bench)
    services.create_planned_sets(active, squat)
    active.current_exercise = squat
    active.save(update_fields=["current_exercise"])

    active.sets.filter(exercise=squat).update(done=True, reps=5)

    assert services.live_context(active)["current_group"]["exercise"] == bench
