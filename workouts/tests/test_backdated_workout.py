"""Тренировка за прошедший день: запись черновика задним числом и правка времени.

Так в приложение переезжает бумажная тетрадка. Состав набирается обычными
экранами живого режима, а здесь спрашивается только то, чего у черновика нет по
определению: когда это было и сколько длилось.
"""

from datetime import datetime, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts import services, stats
from workouts.models import Exercise, Sport, Workout
from workouts.tests.factories import (
    ExerciseFactory,
    SportFactory,
    StrengthSetFactory,
    TimeSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def strength():
    return SportFactory(name="Силовая", category=Sport.Category.STRENGTH, owner=None)


def draft(user, sport, **kwargs):
    """Черновик: ни начала, ни длительности."""
    return WorkoutFactory(user=user, sport=sport, started_at=None, duration_min=None, **kwargs)


def form_data(**overrides):
    """Поля модалки «когда была тренировка»."""
    yesterday = timezone.localdate() - timedelta(days=1)
    data = {
        "date": yesterday.isoformat(),
        "time": "",
        "duration_hours": "1",
        "duration_minutes": "10",
    }
    return data | overrides


def backdate(client, workout, **overrides):
    return client.post(reverse("workout_backdate", args=[workout.pk]), form_data(**overrides))


# ---------- Запись черновика ----------


def test_backdate_records_draft_with_given_datetime(client, user, strength):
    """Черновик становится записанной тренировкой того дня и часа, что указали."""
    workout = draft(user, strength)
    StrengthSetFactory(workout=workout, done=False)
    yesterday = timezone.localdate() - timedelta(days=1)
    client.force_login(user)

    response = backdate(client, workout, time="19:30")

    workout.refresh_from_db()
    started_at = timezone.localtime(workout.started_at)
    assert response["HX-Redirect"] == reverse("workout_summary", args=[workout.pk])
    assert (started_at.date(), started_at.hour, started_at.minute) == (yesterday, 19, 30)
    assert workout.duration_min == 70
    assert workout.is_finished


def test_empty_time_means_noon_for_a_past_day(client, user, strength):
    """Время из тетрадки обычно не вспомнить — тогда ставим полдень."""
    workout = draft(user, strength)
    StrengthSetFactory(workout=workout, done=False)
    client.force_login(user)

    backdate(client, workout)

    workout.refresh_from_db()
    assert timezone.localtime(workout.started_at).hour == 12


def test_empty_time_means_now_for_today(client, user, strength):
    """А сегодняшней тренировке полдень не годится: вечерняя уехала бы в прошлое."""
    workout = draft(user, strength)
    StrengthSetFactory(workout=workout, done=False)
    client.force_login(user)

    backdate(client, workout, date=timezone.localdate().isoformat())

    workout.refresh_from_db()
    assert timezone.localtime(workout.started_at) - timezone.localtime() < timedelta(minutes=1)


def test_all_sets_become_done_without_done_at(client, user, strength):
    """Метки времени подхода остаются пустыми: в тетрадке их нет и выдумывать нечего."""
    workout = draft(user, strength)
    StrengthSetFactory(workout=workout, done=False)
    StrengthSetFactory(workout=workout, done=False)
    client.force_login(user)

    backdate(client, workout)

    rows = workout.sets.all()
    assert len(rows) == 2
    assert all(row.done for row in rows)
    assert all(row.done_at is None for row in rows)


def test_exercise_order_follows_the_order_of_adding(client, user, strength):
    """Без меток времени порядок упражнений задаёт порядок набора — и это верно."""
    workout = draft(user, strength)
    squat = ExerciseFactory(name="Присед", owner=None)
    press = ExerciseFactory(name="Жим лёжа", owner=None)
    StrengthSetFactory(workout=workout, exercise=squat, done=False)
    StrengthSetFactory(workout=workout, exercise=press, done=False)
    client.force_login(user)

    backdate(client, workout)

    workout.refresh_from_db()
    assert [group["exercise"].name for group in services.exercise_groups(workout)] == [
        "Присед",
        "Жим лёжа",
    ]


def test_backdate_is_allowed_while_another_workout_is_live(client, user, strength):
    """Дозаписать тетрадку можно и посреди сегодняшней тренировки.

    Начало и длительность проставляются одним UPDATE, поэтому строка ни на
    мгновение не выглядит идущей и в unique_live_workout_per_user не упирается.
    """
    live = WorkoutFactory(user=user, sport=strength, duration_min=None)
    workout = draft(user, strength)
    StrengthSetFactory(workout=workout, done=False)
    client.force_login(user)

    backdate(client, workout)

    workout.refresh_from_db()
    assert workout.is_finished
    assert Workout.objects.live().filter(pk=live.pk).exists()


def test_planned_day_is_cleared(client, user, strength):
    """Плановый день бывает только у черновика — его держит констрейнт."""
    workout = draft(user, strength, planned_for=timezone.localdate())
    StrengthSetFactory(workout=workout, done=False)
    client.force_login(user)

    backdate(client, workout)

    workout.refresh_from_db()
    assert workout.planned_for is None


# ---------- Что записать нельзя ----------


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        pytest.param({"date": ""}, "date", id="no-date"),
        pytest.param({"duration_hours": "", "duration_minutes": ""}, "duration_minutes", id="zero"),
        pytest.param({"duration_hours": "25"}, "duration_hours", id="too-long"),
    ],
)
def test_invalid_form_keeps_the_draft(client, user, strength, overrides, field):
    workout = draft(user, strength)
    StrengthSetFactory(workout=workout, done=False)
    client.force_login(user)

    response = backdate(client, workout, **overrides)

    workout.refresh_from_db()
    assert field in response.context["form"].errors
    assert workout.is_planned


def test_future_date_is_rejected(client, user, strength):
    workout = draft(user, strength)
    StrengthSetFactory(workout=workout, done=False)
    client.force_login(user)

    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    response = backdate(client, workout, date=tomorrow)

    workout.refresh_from_db()
    assert "будущем" in str(response.context["form"].errors["date"])
    assert workout.is_planned


def test_today_with_a_future_time_is_rejected(client, user, strength):
    """Проверяется собранный момент: сегодняшнее число с 23:00 утром — будущее."""
    workout = draft(user, strength)
    StrengthSetFactory(workout=workout, done=False)
    client.force_login(user)

    moment = timezone.localtime() + timedelta(hours=2)
    response = backdate(
        client, workout, date=timezone.localdate().isoformat(), time=moment.strftime("%H:%M")
    )

    workout.refresh_from_db()
    assert "будущем" in str(response.context["form"].errors["date"])
    assert workout.is_planned


def test_draft_without_sets_is_not_recorded_and_not_deleted(client, user, strength):
    """Черновик подготовлен осознанно — молча удалять его, как делает завершение, нельзя."""
    workout = draft(user, strength)
    client.force_login(user)

    response = backdate(client, workout)

    workout.refresh_from_db()
    assert "нет упражнений" in response.context["error"]
    assert workout.is_planned


@pytest.mark.parametrize(
    ("factory", "fields", "text"),
    [
        pytest.param(StrengthSetFactory, {"reps": 0}, "повторения", id="weight-reps"),
        pytest.param(TimeSetFactory, {"duration_sec": 0}, "время", id="time"),
    ],
)
def test_set_without_a_value_names_its_exercise(client, user, strength, factory, fields, text):
    """Потерянная при переносе тетрадки строка незаметна — поэтому ошибка, а не пропуск."""
    workout = draft(user, strength)
    plank = ExerciseFactory(name="Планка", owner=None, measurement=Exercise.Measurement.TIME)
    factory(workout=workout, exercise=plank, done=False, **fields)
    client.force_login(user)

    response = backdate(client, workout)

    workout.refresh_from_db()
    assert "Планка" in response.context["error"]
    assert text in response.context["error"].lower()
    assert workout.is_planned


def test_second_backdate_is_404(client, user, strength):
    """Даблтап и устаревшая вкладка: записанная тренировка черновиком уже не является."""
    workout = draft(user, strength)
    StrengthSetFactory(workout=workout, done=False)
    client.force_login(user)

    backdate(client, workout)
    response = backdate(client, workout)

    assert response.status_code == 404


def test_cardio_draft_cannot_be_backdated(client, user):
    """У кардио тренировка вводится формой целиком — второго пути не заводим."""
    bike = SportFactory(name="Велосипед", category=Sport.Category.CARDIO, owner=None)
    workout = draft(user, bike)
    client.force_login(user)

    response = client.get(reverse("workout_backdate", args=[workout.pk]))

    assert response.status_code == 404


@pytest.mark.parametrize("method", ["get", "post"])
def test_foreign_draft_cannot_be_backdated(client, user, other_user, strength, method):
    alien = draft(other_user, strength)
    StrengthSetFactory(workout=alien, done=False)

    client.force_login(user)
    response = getattr(client, method)(reverse("workout_backdate", args=[alien.pk]), form_data())

    alien.refresh_from_db()
    assert response.status_code == 404
    assert alien.is_planned


# ---------- Правка времени записанной ----------


def test_time_edit_changes_started_at_and_duration(client, user, strength):
    workout = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=workout)
    week_ago = timezone.localdate() - timedelta(days=7)
    client.force_login(user)

    response = client.post(
        reverse("workout_time", args=[workout.pk]),
        form_data(date=week_ago.isoformat(), time="08:15", duration_hours="0"),
    )

    workout.refresh_from_db()
    started_at = timezone.localtime(workout.started_at)
    assert response["HX-Redirect"] == reverse("workout_summary", args=[workout.pk])
    assert (started_at.date(), started_at.hour, started_at.minute) == (week_ago, 8, 15)
    assert workout.duration_min == 10


def test_time_edit_shifts_done_at_marks(client, user, strength):
    """Метки живого режима настоящие: в прежнем дне им оставаться незачем."""
    started_at = timezone.now() - timedelta(days=1)
    workout = WorkoutFactory(user=user, sport=strength, started_at=started_at, duration_min=60)
    marked = StrengthSetFactory(workout=workout, done_at=started_at + timedelta(minutes=5))
    unmarked = StrengthSetFactory(workout=workout, done_at=None)
    target = timezone.localdate() - timedelta(days=10)
    client.force_login(user)

    client.post(
        reverse("workout_time", args=[workout.pk]),
        form_data(date=target.isoformat(), time="10:00"),
    )

    marked.refresh_from_db()
    unmarked.refresh_from_db()
    workout.refresh_from_db()
    assert timezone.localtime(marked.done_at).date() == target
    assert marked.done_at - workout.started_at == timedelta(minutes=5)
    assert unmarked.done_at is None


def test_time_edit_form_is_prefilled(client, user, strength):
    started_at = timezone.make_aware(datetime(2026, 8, 12, 18, 40))
    workout = WorkoutFactory(user=user, sport=strength, started_at=started_at, duration_min=95)
    client.force_login(user)

    response = client.get(reverse("workout_time", args=[workout.pk]))

    initial = response.context["form"].initial
    assert initial["date"].isoformat() == "2026-08-12"
    assert initial["time"].strftime("%H:%M") == "18:40"
    assert (initial["duration_hours"], initial["duration_minutes"]) == (1, 35)


@pytest.mark.parametrize(
    "state",
    [
        pytest.param("planned", id="draft"),
        pytest.param("live", id="live"),
        pytest.param("cardio", id="cardio"),
    ],
)
def test_time_edit_is_404_for_everything_but_recorded_strength(client, user, strength, state):
    if state == "planned":
        workout = draft(user, strength)
    elif state == "live":
        workout = WorkoutFactory(user=user, sport=strength, duration_min=None)
    else:
        bike = SportFactory(name="Велосипед", category=Sport.Category.CARDIO, owner=None)
        workout = WorkoutFactory(user=user, sport=bike)
    client.force_login(user)

    response = client.get(reverse("workout_time", args=[workout.pk]))

    assert response.status_code == 404


@pytest.mark.parametrize("method", ["get", "post"])
def test_foreign_workout_time_is_404(client, user, other_user, strength, method):
    theirs = WorkoutFactory(user=other_user, sport=strength, duration_min=60)
    before = theirs.started_at

    client.force_login(user)
    response = getattr(client, method)(reverse("workout_time", args=[theirs.pk]), form_data())

    theirs.refresh_from_db()
    assert response.status_code == 404
    assert theirs.started_at == before


# ---------- Место в агрегатах ----------


def test_backdated_workout_lands_in_its_own_week(client, user, strength):
    """В ленте истории запись встаёт в неделю своей даты, а не даты внесения."""
    workout = draft(user, strength)
    StrengthSetFactory(workout=workout, done=False)
    long_ago = timezone.localdate() - timedelta(days=30)
    client.force_login(user)

    backdate(client, workout, date=long_ago.isoformat())

    response = client.get(reverse("workout_history"))
    groups = response.context["groups"]
    assert [group["key"] for group in groups] == [stats.week_start(long_ago).isoformat()]
    assert [item.pk for item in groups[0]["items"]] == [workout.pk]


def test_backdated_workout_does_not_override_last_sets(client, user, strength):
    """Подстановка весов смотрит на самую свежую тренировку, а не на внесённую последней."""
    press = ExerciseFactory(name="Жим лёжа", owner=None)
    fresh = WorkoutFactory(user=user, sport=strength)
    StrengthSetFactory(workout=fresh, exercise=press, weight_kg=90)
    old = draft(user, strength)
    StrengthSetFactory(workout=old, exercise=press, weight_kg=60, done=False)
    client.force_login(user)

    backdate(client, old, date=(timezone.localdate() - timedelta(days=60)).isoformat())

    assert [row.weight_kg for row in services.last_sets(user, press)] == [90]


def test_backdated_workout_counts_in_exercise_progress(client, user, strength):
    """Точка прогресса появляется на своём месте по дате."""
    press = ExerciseFactory(name="Жим лёжа", owner=None)
    fresh = WorkoutFactory(user=user, sport=strength)
    StrengthSetFactory(workout=fresh, exercise=press, weight_kg=90)
    old = draft(user, strength)
    StrengthSetFactory(workout=old, exercise=press, weight_kg=60, done=False)
    long_ago = timezone.localdate() - timedelta(days=45)
    client.force_login(user)

    backdate(client, old, date=long_ago.isoformat())

    progress = stats.exercise_progress(user, press)
    assert [point["date"] for point in progress][0] == long_ago
