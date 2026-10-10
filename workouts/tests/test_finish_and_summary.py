"""Завершение живого режима и экран-итог силовой тренировки."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.models import Workout
from workouts.tests.factories import (
    CardioPartFactory,
    ExerciseFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def active_started_ago(user, minutes):
    return WorkoutFactory(
        user=user, duration_min=None, started_at=timezone.now() - timedelta(minutes=minutes)
    )


def test_finish_computes_duration_and_deletes_undone_sets(client, user):
    client.force_login(user)
    workout = active_started_ago(user, 40)
    exercise = ExerciseFactory()
    done = StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=True)
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=2, done=False)

    response = client.post(reverse("workout_finish", args=[workout.pk]))

    workout.refresh_from_db()
    assert response.status_code == 302
    assert response.url == reverse("workout_summary", args=[workout.pk])
    assert workout.duration_min in (40, 41)
    assert list(workout.sets.all()) == [done]


def test_finish_duration_is_at_least_one_minute(client, user):
    client.force_login(user)
    workout = active_started_ago(user, 0)
    StrengthSetFactory(workout=workout, set_number=1, done=True)

    client.post(reverse("workout_finish", args=[workout.pk]))

    workout.refresh_from_db()
    assert workout.duration_min == 1


def test_finish_with_no_done_sets_deletes_workout(client, user):
    client.force_login(user)
    workout = active_started_ago(user, 10)
    StrengthSetFactory(workout=workout, set_number=1, done=False)

    response = client.post(reverse("workout_finish", args=[workout.pk]))

    assert response.status_code == 302
    assert response.url == reverse("workout_history")
    assert not Workout.objects.filter(pk=workout.pk).exists()


def test_finish_twice_is_idempotent(client, user):
    client.force_login(user)
    workout = active_started_ago(user, 30)
    StrengthSetFactory(workout=workout, set_number=1, done=True)

    client.post(reverse("workout_finish", args=[workout.pk]))
    workout.refresh_from_db()
    duration = workout.duration_min

    response = client.post(reverse("workout_finish", args=[workout.pk]))

    workout.refresh_from_db()
    assert response.status_code == 302
    assert workout.duration_min == duration


def test_finish_of_other_users_workout_is_404(client, user, other_user):
    client.force_login(user)
    alien = WorkoutFactory(user=other_user, duration_min=None)

    response = client.post(reverse("workout_finish", args=[alien.pk]))

    alien.refresh_from_db()
    assert response.status_code == 404
    assert alien.duration_min is None


def test_finish_modal_shows_done_count(client, user):
    client.force_login(user)
    workout = active_started_ago(user, 10)
    exercise = ExerciseFactory()
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=True)
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=2, done=True)

    content = client.get(reverse("workout_finish", args=[workout.pk])).content.decode()

    assert "Выполнено подходов: 2" in content


# ---------- Забытая тренировка ----------


def test_live_screen_flags_a_forgotten_workout(client, user):
    """Идёт дольше четырёх часов — плашка «похоже, забыли» с «Завершить»."""
    client.force_login(user)
    fresh = active_started_ago(user, 3 * 60)

    assert (
        "забыли завершить"
        not in client.get(reverse("workout_live", args=[fresh.pk])).content.decode()
    )

    fresh.started_at = timezone.now() - timedelta(hours=28)
    fresh.save(update_fields=["started_at"])
    content = client.get(reverse("workout_live", args=[fresh.pk])).content.decode()
    assert "Тренировка идёт уже 28 ч — похоже, её забыли завершить." in content


def test_forgotten_workout_asks_duration_with_estimate(client, user):
    """Окно забытой подставляет время до последнего выполненного подхода."""
    client.force_login(user)
    workout = active_started_ago(user, 28 * 60)
    StrengthSetFactory(
        workout=workout,
        set_number=1,
        done=True,
        done_at=workout.started_at + timedelta(minutes=64, seconds=10),
    )

    response = client.get(reverse("workout_finish", args=[workout.pk]))

    assert response.context["form"].initial == {"duration_hours": 1, "duration_minutes": 5}
    assert 'hx-post="' in response.content.decode()


def test_forgotten_workout_is_finished_with_given_duration(client, user):
    client.force_login(user)
    workout = active_started_ago(user, 28 * 60)
    StrengthSetFactory(workout=workout, set_number=1, done=True)
    url = reverse("workout_finish", args=[workout.pk])

    response = client.post(
        url, {"duration_hours": "1", "duration_minutes": "10"}, HTTP_HX_REQUEST="true"
    )

    workout.refresh_from_db()
    assert response.headers["HX-Redirect"] == reverse("workout_summary", args=[workout.pk])
    assert workout.duration_min == 70


def test_forgotten_empty_workout_is_discarded_without_asking(client, user):
    """Нечего записывать — нечего и спрашивать: завершение просто стирает её."""
    client.force_login(user)
    workout = active_started_ago(user, 28 * 60)
    StrengthSetFactory(workout=workout, set_number=1, done=False)

    assert client.get(reverse("workout_finish", args=[workout.pk])).context["form"] is None
    client.post(reverse("workout_finish", args=[workout.pk]))

    assert not Workout.objects.filter(pk=workout.pk).exists()


@pytest.mark.parametrize(
    ("hours", "minutes", "error"),
    [
        ("", "", "Укажите длительность тренировки."),
        ("6", "0", "Тренировка идёт меньше — проверьте время."),
    ],
)
def test_forgotten_workout_needs_a_sane_duration(client, user, hours, minutes, error):
    client.force_login(user)
    workout = active_started_ago(user, 5 * 60)
    StrengthSetFactory(workout=workout, set_number=1, done=True)

    response = client.post(
        reverse("workout_finish", args=[workout.pk]),
        {"duration_hours": hours, "duration_minutes": minutes},
        HTTP_HX_REQUEST="true",
    )

    workout.refresh_from_db()
    assert error in response.content.decode()
    assert workout.duration_min is None


def test_summary_shows_exercises_and_total_tonnage(client, user):
    client.force_login(user)
    workout = WorkoutFactory(user=user, duration_min=62)
    bench = ExerciseFactory(name="Жим лёжа")
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1, weight_kg=80, reps=8)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=2, weight_kg=80, reps=8)

    content = client.get(reverse("workout_summary", args=[workout.pk])).content.decode()

    assert "Жим лёжа" in content
    assert "1280" in content  # 2 × 80 кг × 8
    assert "1:02" in content


def test_summary_of_active_workout_redirects_to_live(client, user):
    client.force_login(user)
    workout = WorkoutFactory(user=user, duration_min=None)

    response = client.get(reverse("workout_summary", args=[workout.pk]))

    assert response.status_code == 302
    assert response.url == reverse("workout_live", args=[workout.pk])


def test_summary_of_other_users_workout_is_404(client, user, other_user):
    client.force_login(user)
    alien = WorkoutFactory(user=other_user)

    response = client.get(reverse("workout_summary", args=[alien.pk]))

    assert response.status_code == 404


def test_summary_of_cardio_leads_to_its_form(client, user):
    """Адрес итога безопасен для любой записанной тренировки.

    Раньше здесь был 404, и старая ссылка на кардио ломалась. Теперь экран
    выбирается содержимым: подходов нет — значит дом это форма кардио.
    """
    client.force_login(user)
    cardio = CardioPartFactory(workout__user=user).workout

    response = client.get(reverse("workout_summary", args=[cardio.pk]))

    assert response.status_code == 302
    assert response.url == reverse("workout_edit", args=[cardio.pk])
