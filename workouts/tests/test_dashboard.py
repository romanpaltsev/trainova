"""Дашборд: сводка, партиал недели по тапу на столбец, страница упражнения."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts import stats
from workouts.tests.factories import (
    ExerciseFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def test_dashboard_renders_for_new_user_without_workouts(client, user):
    client.force_login(user)

    response = client.get(reverse("dashboard"))

    content = response.content.decode()
    assert response.status_code == 200
    assert "Записывайте тренировки" in content
    assert "weekly-chart" not in content
    # Секции не исчезают молча: у каждой — подсказка первого шага.
    assert "Рекорды появятся после первой силовой" in content
    assert "Здесь появятся ваши тренировки" in content


def test_dashboard_shows_week_summary_and_latest_workouts(client, user):
    workout = WorkoutFactory(user=user, duration_min=62)
    StrengthSetFactory(workout=workout, set_number=1, weight_kg=80, reps=10)

    client.force_login(user)
    content = client.get(reverse("dashboard")).content.decode()

    assert "За 7 дней" in content
    assert "1:02" in content
    assert "Часы по неделям" in content
    assert "weekly-chart" in content
    assert "Личные рекорды" in content
    assert f'data-workout="{workout.pk}"' in content


def test_dashboard_hides_other_users_data(client, user, other_user):
    alien = WorkoutFactory(user=other_user)
    alien_exercise = ExerciseFactory(owner=other_user, name="Чужое упражнение")
    StrengthSetFactory(workout=alien, exercise=alien_exercise, set_number=1, weight_kg=200, reps=1)

    client.force_login(user)
    content = client.get(reverse("dashboard")).content.decode()

    assert f'data-workout="{alien.pk}"' not in content
    assert "Чужое упражнение" not in content
    assert "200 кг" not in content


def test_dashboard_redirects_anonymous_to_login(client):
    response = client.get(reverse("dashboard"))

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_week_partial_shows_only_workouts_of_requested_week(client, user):
    today = timezone.localdate()
    monday = stats.week_start(today)
    inside = WorkoutFactory(user=user, started_at=timezone.now())
    outside = WorkoutFactory(user=user, started_at=timezone.now() - timedelta(days=14))

    client.force_login(user)
    content = client.get(reverse("dashboard_week"), {"start": monday.isoformat()}).content.decode()

    assert f'data-workout="{inside.pk}"' in content
    assert f'data-workout="{outside.pk}"' not in content


def test_week_partial_hides_other_users_workouts(client, user, other_user):
    monday = stats.week_start(timezone.localdate())
    alien = WorkoutFactory(user=other_user, started_at=timezone.now())

    client.force_login(user)
    content = client.get(reverse("dashboard_week"), {"start": monday.isoformat()}).content.decode()

    assert f'data-workout="{alien.pk}"' not in content
    assert "тренировок не было" in content


@pytest.mark.parametrize("start", ["", "abc", "2026-13-40"], ids=["empty", "text", "bad-date"])
def test_week_partial_rejects_bad_start_param(client, user, start):
    client.force_login(user)

    response = client.get(reverse("dashboard_week"), {"start": start})

    assert response.status_code == 400


def test_week_partial_without_param_is_rejected(client, user):
    client.force_login(user)

    assert client.get(reverse("dashboard_week")).status_code == 400


def test_week_partial_rejects_date_at_calendar_edge(client, user):
    """Год 9999 переполняет арифметику недели — это тоже негодный ввод, не 500."""
    client.force_login(user)

    response = client.get(reverse("dashboard_week"), {"start": "9999-12-31"})

    assert response.status_code == 400


def test_week_partial_redirects_anonymous_to_login(client):
    monday = stats.week_start(timezone.localdate())

    response = client.get(reverse("dashboard_week"), {"start": monday.isoformat()})

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_exercise_page_shows_chart_and_set_history(client, user):
    bench = ExerciseFactory(name="Жим лёжа")
    for offset, weight in ((7, 70), (1, Decimal("77.5"))):
        workout = WorkoutFactory(user=user, started_at=timezone.now() - timedelta(days=offset))
        StrengthSetFactory(workout=workout, exercise=bench, set_number=1, weight_kg=weight, reps=8)

    client.force_login(user)
    content = client.get(reverse("exercise_detail", args=[bench.pk])).content.decode()

    assert "Жим лёжа" in content
    assert "рекорд 77,5 кг" in content
    assert 'id="exercise-chart"' in content  # canvas, а не только json_script
    assert "История подходов" in content
    assert "77,5" in content


def test_exercise_page_returns_404_for_foreign_personal_exercise(client, user, other_user):
    alien = ExerciseFactory(owner=other_user)

    client.force_login(user)

    assert client.get(reverse("exercise_detail", args=[alien.pk])).status_code == 404


def test_exercise_page_with_global_exercise_shows_only_own_sets(client, user, other_user):
    bench = ExerciseFactory(name="Жим лёжа")  # глобальное — видно обоим
    mine = WorkoutFactory(user=user)
    StrengthSetFactory(workout=mine, exercise=bench, set_number=1, weight_kg=80, reps=8)
    alien = WorkoutFactory(user=other_user)
    StrengthSetFactory(workout=alien, exercise=bench, set_number=1, weight_kg=200, reps=1)

    client.force_login(user)
    content = client.get(reverse("exercise_detail", args=[bench.pk])).content.decode()

    assert "рекорд 80 кг" in content
    assert "200" not in content


def test_exercise_page_renders_without_data(client, user):
    bench = ExerciseFactory()

    client.force_login(user)
    response = client.get(reverse("exercise_detail", args=[bench.pk]))

    content = response.content.decode()
    assert response.status_code == 200
    assert "ещё не было в тренировках" in content
    assert "exercise-chart-data" in content  # json_script есть, canvas — нет
    assert 'id="exercise-chart"' not in content


def test_exercise_page_redirects_anonymous_to_login(client):
    bench = ExerciseFactory()

    response = client.get(reverse("exercise_detail", args=[bench.pk]))

    assert response.status_code == 302
    assert reverse("account_login") in response.url
