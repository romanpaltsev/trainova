"""Смешанная тренировка: силовая и кардио в одной записи.

Занятие «разминка бегом → штанга → заминка на велосипеде» — это одна
тренировка, а не две записи за день. Кардио-часть живёт своей строкой со своим
видом спорта и своим временем, а «смешанность» нигде не хранится флагом:
она выводится из того, что у тренировки есть и подходы, и части.
"""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.models import CardioPart, Sport, Workout
from workouts.tests.factories import (
    CardioPartFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def strength():
    return SportFactory(name="Силовая", category=Sport.Category.STRENGTH, owner=None)


@pytest.fixture
def run():
    return SportFactory(name="Бег", category=Sport.Category.CARDIO, owner=None)


def part_data(sport, **overrides):
    data = {
        "sport": str(sport.pk),
        "distance_km": "5",
        "avg_heart_rate": "",
        "duration_hours": "0",
        "duration_minutes": "25",
    }
    return data | overrides


def add_part(client, workout, sport, **overrides):
    return client.post(
        reverse("workout_cardio_part", args=[workout.pk]), part_data(sport, **overrides)
    )


# ---------- Ввод ----------


def test_cardio_is_added_to_a_running_workout(client, user, strength, run):
    """Главный сценарий: одна запись, одна дата, одно место."""
    workout = WorkoutFactory(user=user, sport=strength, duration_min=None)
    StrengthSetFactory(workout=workout, set_number=1)
    client.force_login(user)

    response = add_part(client, workout, run)

    part = workout.cardio_parts.get()
    assert response["HX-Redirect"] == reverse("workout_live", args=[workout.pk])
    assert (part.sport, part.duration_min, part.distance_km) == (run, 25, Decimal("5.00"))
    assert Workout.objects.filter(user=user).count() == 1


def test_cardio_is_added_to_a_recorded_workout(client, user, strength, run):
    """Заминку дописывают и постфактум — экрана правки силовой в проекте нет."""
    workout = WorkoutFactory(user=user, sport=strength, duration_min=90)
    StrengthSetFactory(workout=workout, set_number=1)
    client.force_login(user)

    response = add_part(client, workout, run)

    assert response["HX-Redirect"] == reverse("workout_summary", args=[workout.pk])
    assert workout.cardio_parts.count() == 1


def test_pace_of_a_part_uses_its_own_time(client, user, strength, run):
    """Пять километров за 25 минут — это 5:00, а не темп по всей тренировке."""
    workout = WorkoutFactory(user=user, sport=strength, duration_min=90)
    StrengthSetFactory(workout=workout, set_number=1)
    client.force_login(user)

    add_part(client, workout, run)

    assert workout.cardio_parts.get().metric_display == "5:00 /км"


def test_several_parts_keep_the_order_they_were_added(client, user, strength, run):
    """Порядок частей — порядок ввода: «сначала пробежка» и значит добавить её первой."""
    bike = SportFactory(name="Велосипед", category=Sport.Category.CARDIO, owner=None)
    workout = WorkoutFactory(user=user, sport=strength, duration_min=None)
    StrengthSetFactory(workout=workout, set_number=1)
    client.force_login(user)

    add_part(client, workout, run)
    add_part(client, workout, bike, distance_km="12")

    assert [part.sport.name for part in workout.cardio_parts.all()] == ["Бег", "Велосипед"]


def test_part_without_distance_is_legal(client, user, strength, run):
    """«Двадцать минут на дорожке, не мерил» — законная часть."""
    workout = WorkoutFactory(user=user, sport=strength, duration_min=None)
    client.force_login(user)

    add_part(client, workout, run, distance_km="")

    part = workout.cardio_parts.get()
    assert part.distance_km is None
    assert part.metric_display == "—"


def test_part_is_edited_and_removed(client, user, strength, run):
    workout = WorkoutFactory(user=user, sport=strength, duration_min=90)
    StrengthSetFactory(workout=workout, set_number=1)
    part = CardioPartFactory(workout=workout, sport=run, duration_min=20, distance_km=4)
    client.force_login(user)

    url = reverse("workout_cardio_part_edit", args=[workout.pk, part.pk])
    client.post(url, part_data(run, distance_km="6", duration_minutes="30"))
    part.refresh_from_db()
    assert (part.distance_km, part.duration_min) == (Decimal("6.00"), 30)

    client.post(url, {"delete": "1"})
    assert not workout.cardio_parts.exists()


def test_edit_form_is_prefilled(client, user, strength, run):
    workout = WorkoutFactory(user=user, sport=strength, duration_min=90)
    part = CardioPartFactory(workout=workout, sport=run, duration_min=95, distance_km=12)
    client.force_login(user)

    form = client.get(reverse("workout_cardio_part_edit", args=[workout.pk, part.pk])).context[
        "form"
    ]

    assert form.initial["duration_hours"] == 1
    assert form.initial["duration_minutes"] == 35


@pytest.mark.parametrize(
    ("field", "overrides"),
    [
        pytest.param("sport", {"sport": ""}, id="no-sport"),
        pytest.param("distance_km", {"distance_km": "0"}, id="zero-distance"),
        pytest.param("distance_km", {"distance_km": "abc"}, id="garbage-distance"),
        pytest.param("duration_hours", {"duration_hours": "25"}, id="too-long"),
    ],
)
def test_invalid_part_is_not_saved(client, user, strength, run, field, overrides):
    workout = WorkoutFactory(user=user, sport=strength, duration_min=None)
    client.force_login(user)

    response = client.post(
        reverse("workout_cardio_part", args=[workout.pk]), part_data(run) | overrides
    )

    assert field in response.context["form"].errors
    assert not CardioPart.objects.exists()


def test_strength_sport_cannot_become_a_cardio_part(client, user, strength):
    """Иначе в рекордах появилась бы плитка «Силовая · дистанция»."""
    workout = WorkoutFactory(user=user, sport=strength, duration_min=None)
    client.force_login(user)

    response = client.post(reverse("workout_cardio_part", args=[workout.pk]), part_data(strength))

    assert "sport" in response.context["form"].errors
    assert not CardioPart.objects.exists()


def test_other_users_sport_cannot_become_a_part(client, user, other_user, strength):
    workout = WorkoutFactory(user=user, sport=strength, duration_min=None)
    alien = SportFactory(name="Каяк", category=Sport.Category.CARDIO, owner=other_user)
    client.force_login(user)

    response = add_part(client, workout, alien)

    assert "sport" in response.context["form"].errors
    assert not CardioPart.objects.exists()


# ---------- Изоляция данных ----------


@pytest.mark.parametrize("method", ["get", "post"])
def test_foreign_workout_cardio_is_404(client, user, other_user, strength, run, method):
    theirs = WorkoutFactory(user=other_user, sport=strength, duration_min=60)

    client.force_login(user)
    response = getattr(client, method)(
        reverse("workout_cardio_part", args=[theirs.pk]), part_data(run)
    )

    assert response.status_code == 404
    assert not theirs.cardio_parts.exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_part_of_another_workout_is_404(client, user, strength, run, method):
    """Часть ищется среди частей ЭТОЙ тренировки — подставить свою чужой нельзя."""
    mine = WorkoutFactory(user=user, sport=strength, duration_min=60)
    other = WorkoutFactory(user=user, sport=strength, duration_min=60)
    part = CardioPartFactory(workout=other, sport=run, duration_min=20)

    client.force_login(user)
    response = getattr(client, method)(
        reverse("workout_cardio_part_edit", args=[mine.pk, part.pk]), part_data(run)
    )

    assert response.status_code == 404
    assert CardioPart.objects.filter(pk=part.pk).exists()


# ---------- Навигация выбирается содержимым ----------


def test_mixed_workout_lives_on_the_live_screen_and_summary(client, user, strength, run):
    workout = WorkoutFactory(user=user, sport=strength, duration_min=None)
    StrengthSetFactory(workout=workout, set_number=1)
    CardioPartFactory(workout=workout, sport=run, duration_min=20)
    client.force_login(user)

    assert client.get(reverse("workout_live", args=[workout.pk])).status_code == 200
    client.post(reverse("workout_finish", args=[workout.pk]))

    workout.refresh_from_db()
    assert workout.is_finished
    assert client.get(reverse("workout_summary", args=[workout.pk])).status_code == 200
    assert client.get(reverse("workout_edit", args=[workout.pk])).status_code == 404


def test_cardio_only_workout_redirects_from_summary_to_its_form(client, user, run):
    """Адрес итога безопасен для любой тренировки: старые ссылки не ломаются."""
    workout = CardioPartFactory(workout__user=user, workout__sport=run).workout
    client.force_login(user)

    response = client.get(reverse("workout_summary", args=[workout.pk]))

    assert response.status_code == 302
    assert response["Location"] == reverse("workout_edit", args=[workout.pk])


def test_finishing_keeps_a_workout_that_has_only_cardio(client, user, strength, run):
    """Успели только пробежку — тренировка записывается, а не стирается."""
    workout = WorkoutFactory(user=user, sport=strength, duration_min=None)
    CardioPartFactory(workout=workout, sport=run, duration_min=20, distance_km=4)
    client.force_login(user)

    response = client.post(reverse("workout_finish", args=[workout.pk]), follow=True)

    workout.refresh_from_db()
    assert workout.is_finished
    assert response.redirect_chain[0][0] == reverse("workout_edit", args=[workout.pk])


def test_mixed_workout_time_is_edited_on_the_summary(client, user, strength, run):
    workout = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=workout, set_number=1)
    CardioPartFactory(workout=workout, sport=run, duration_min=20)
    client.force_login(user)

    assert client.get(reverse("workout_time", args=[workout.pk])).status_code == 200


def test_repeat_copies_exercises_but_not_cardio(client, user, strength, run):
    """Дистанция — результат, а не план: её не копируют, как веса и место."""
    source = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=source, set_number=1)
    CardioPartFactory(workout=source, sport=run, duration_min=20, distance_km=4)
    client.force_login(user)

    client.post(reverse("workout_repeat", args=[source.pk]))

    fresh = Workout.objects.filter(user=user).exclude(pk=source.pk).get()
    assert fresh.sets.exists()
    assert not fresh.cardio_parts.exists()


# ---------- Смешанная в ленте и справочниках ----------


def test_history_filter_finds_a_mixed_workout_by_its_cardio_sport(client, user, strength, run):
    """У смешанной вид спорта силовой, но по чипу «Бег» она найтись обязана."""
    mixed = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=mixed, set_number=1)
    CardioPartFactory(workout=mixed, sport=run, duration_min=20, distance_km=4)
    plain = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=plain, set_number=1)
    client.force_login(user)

    content = client.get(f"{reverse('workout_history')}?sport={run.pk}").content.decode()

    assert f'id="workout-{mixed.pk}"' in content
    assert f'id="workout-{plain.pk}"' not in content


def test_history_chips_include_cardio_part_sports(client, user, strength, run):
    mixed = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=mixed, set_number=1)
    CardioPartFactory(workout=mixed, sport=run, duration_min=20)
    client.force_login(user)

    chips = client.get(reverse("workout_history")).context["sports_used"]

    assert set(chips) == {strength, run}


def test_mixed_workout_appears_once_in_history(client, user, strength, run):
    """Джойн по частям не должен размножить карточку."""
    mixed = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=mixed, set_number=1)
    CardioPartFactory(workout=mixed, sport=run, duration_min=20)
    CardioPartFactory(workout=mixed, sport=run, duration_min=10)
    client.force_login(user)

    content = client.get(f"{reverse('workout_history')}?sport={run.pk}").content.decode()

    assert content.count(f'id="workout-{mixed.pk}"') == 1


def test_sport_used_only_as_a_part_is_not_called_unused(client, user, strength):
    """PROTECT держит его этой частью — «не использовалось» было бы враньём."""
    mine = SportFactory(name="Гребля", category=Sport.Category.CARDIO, owner=user)
    workout = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=workout, set_number=1)
    CardioPartFactory(workout=workout, sport=mine, duration_min=20)
    client.force_login(user)

    rows = client.get(reverse("my_sports")).context["sports"]

    assert [row.usage_label for row in rows] == ["в 1 тренировке"]


# ---------- Агрегаты ----------


def test_seven_day_summary_counts_a_mixed_workout_once(client, user, strength, run):
    """Одно занятие — одна тренировка, но и тоннаж, и дистанция."""
    from workouts import stats

    today = timezone.localdate()
    mixed = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=mixed, set_number=1, weight_kg=80, reps=10)
    CardioPartFactory(workout=mixed, sport=run, duration_min=20, distance_km=4)

    summary = stats.seven_day_summary(user, today=today)

    assert summary["count"] == 1
    assert summary["strength_count"] == 1
    assert summary["tonnage_display"] == "800"
    assert summary["distance_display"] == "4"
    assert summary["cardio_sports"] == ["Бег"]


def test_weekly_chart_splits_minutes_between_parts_and_owner(user, strength, run):
    from workouts import stats

    today = timezone.localdate()
    mixed = WorkoutFactory(user=user, sport=strength, duration_min=90)
    StrengthSetFactory(workout=mixed, set_number=1)
    CardioPartFactory(workout=mixed, sport=run, duration_min=30, distance_km=6)

    chart = stats.weekly_chart(user, today=today)

    hours = {row["name"]: row["hours"][-1] for row in chart["datasets"]}
    assert hours == {"Силовая": 1.0, "Бег": 0.5}


def test_weekly_chart_never_goes_negative(user, strength, run):
    """Часть длиннее тренировки (правка через админку) не должна дать минус."""
    from workouts import stats

    today = timezone.localdate()
    workout = WorkoutFactory(user=user, sport=strength, duration_min=20)
    CardioPartFactory(workout=workout, sport=run, duration_min=90, distance_km=12)

    chart = stats.weekly_chart(user, today=today)

    assert all(value >= 0 for row in chart["datasets"] for value in row["hours"])


def test_cardio_records_ignore_the_owner_sport(user, strength, run):
    """Рекорд строится по виду спорта части, а не тренировки."""
    from workouts import stats

    workout = WorkoutFactory(user=user, sport=strength, duration_min=90)
    StrengthSetFactory(workout=workout, set_number=1)
    CardioPartFactory(workout=workout, sport=run, duration_min=25, distance_km=5)

    records = stats.cardio_records(user)

    assert [record["name"] for record in records] == ["Бег"]
    assert records[0]["metric_display"] == "5:00 /км"
