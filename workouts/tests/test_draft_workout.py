"""Черновики: подготовка тренировки заранее и переход «Начать тренировку».

Черновик — тренировка без started_at: собрана, но время не идёт. Правится тем же
живым режимом, невидима во всех агрегатах, пока не начата и не завершена.
"""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from workouts import services, stats
from workouts.models import Sport, StrengthSet, Workout
from workouts.tests.factories import (
    ExerciseFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def draft(user, **kwargs):
    """Черновик: ни начала, ни длительности."""
    return WorkoutFactory(user=user, started_at=None, duration_min=None, **kwargs)


def prepare(client, sport):
    return client.post(reverse("strength_prepare"), {"sport": sport.pk})


# ---------- Состояния и инварианты базы ----------


def test_three_states_are_read_from_two_columns(user):
    planned = draft(user)
    live = WorkoutFactory(user=user, duration_min=None)
    finished = WorkoutFactory(user=user)

    assert (planned.is_planned, planned.is_finished) == (True, False)
    assert (live.is_planned, live.is_finished) == (False, False)
    assert (finished.is_planned, finished.is_finished) == (False, True)
    assert list(Workout.objects.filter(user=user).planned()) == [planned]
    assert list(Workout.objects.filter(user=user).live()) == [live]
    assert list(Workout.objects.filter(user=user).finished()) == [finished]


def test_several_drafts_are_allowed(client, user):
    """Планировать пн/ср/пт — законно: уникальный индекс требует начала."""
    client.force_login(user)
    sport = SportFactory()

    prepare(client, sport)
    prepare(client, sport)
    prepare(client, sport)

    assert Workout.objects.filter(user=user).planned().count() == 3


def test_draft_and_live_workout_coexist(user):
    draft(user)
    WorkoutFactory(user=user, duration_min=None)

    assert Workout.objects.filter(user=user).unfinished().count() == 2


def test_started_second_draft_is_rejected_by_database(user):
    """Индекс держит и переход, а не только вставку: гонку ловит база."""
    WorkoutFactory(user=user, duration_min=None)
    second = draft(user)

    with pytest.raises(IntegrityError), transaction.atomic():
        Workout.objects.filter(pk=second.pk).update(started_at=timezone.now())


def test_finished_workout_without_start_is_rejected_by_database(user):
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkoutFactory(user=user, started_at=None, duration_min=45)


def test_drafts_do_not_head_default_ordering(user):
    """В Postgres DESC ставит NULL первыми — без nulls_last черновик возглавил бы
    любую выборку без явной сортировки, и «первая» тренировка была бы не той."""
    finished = WorkoutFactory(user=user)
    draft(user)

    assert Workout.objects.filter(user=user).first() == finished


def test_draft_str_and_elapsed_do_not_invent_time(user):
    planned = draft(user, sport=SportFactory(name="Силовая"))

    assert str(planned) == "Силовая — черновик"
    assert planned.elapsed_min == 0


# ---------- Создание ----------


def test_prepare_creates_draft_without_clock(client, user):
    client.force_login(user)
    sport = SportFactory()

    response = prepare(client, sport)

    workout = Workout.objects.get(user=user)
    assert workout.started_at is None
    assert workout.duration_min is None
    assert response.status_code == 302
    assert response.url == reverse("workout_live", args=[workout.pk])


def test_prepare_of_foreign_or_cardio_sport_is_404(client, user, other_user):
    client.force_login(user)

    assert prepare(client, SportFactory(owner=other_user)).status_code == 404
    assert prepare(client, SportFactory(category=Sport.Category.CARDIO)).status_code == 404
    assert Workout.objects.filter(user=user).exists() is False


def test_prepare_works_while_another_workout_is_running(client, user):
    client.force_login(user)
    live = WorkoutFactory(user=user, duration_min=None)

    prepare(client, SportFactory())

    assert Workout.objects.filter(user=user).planned().count() == 1
    assert Workout.objects.filter(user=user).live().get() == live


# ---------- Экран черновика ----------


def test_draft_screen_has_no_clock_and_offers_start(client, user):
    client.force_login(user)
    bench = ExerciseFactory(name="Жим лёжа")
    planned = draft(user)
    services.create_planned_sets(planned, bench)

    content = client.get(reverse("workout_live", args=[planned.pk])).content.decode()

    assert "черновик · время не идёт" in content
    assert "тренировка идёт" not in content
    assert "liveClock()" not in content
    # Отдыхать до старта нечего — карточки таймера отдыха нет.
    assert reverse("live_rest", args=[planned.pk]) not in content
    # Крупная кнопка стоит там, где в живом режиме «Подход выполнен».
    assert "Начать тренировку" in content
    assert reverse("draft_start", args=[planned.pk]) in content
    assert "Подход выполнен" not in content
    assert reverse("workout_delete", args=[planned.pk]) in content


def test_draft_screen_prefills_sets_from_last_finished_workout(client, user):
    """Подготовка использует то же правило подстановки, что и живой режим."""
    client.force_login(user)
    bench = ExerciseFactory(name="Жим лёжа")
    past = WorkoutFactory(user=user)
    StrengthSetFactory(workout=past, exercise=bench, set_number=1, weight_kg=70, reps=10)
    StrengthSetFactory(
        workout=past, exercise=bench, set_number=2, weight_kg=Decimal("77.5"), reps=8
    )
    planned = draft(user)

    client.post(reverse("live_exercises", args=[planned.pk]), {"name": "Жим лёжа"})

    rows = list(planned.sets.order_by("set_number"))
    assert [(row.weight_kg, row.reps, row.done) for row in rows] == [
        (Decimal("70.00"), 10, False),
        (Decimal("77.50"), 8, False),
    ]


def test_draft_sets_are_adjustable_and_removable(client, user):
    client.force_login(user)
    planned = draft(user)
    row = StrengthSetFactory(workout=planned, set_number=1, weight_kg=70, reps=8, done=False)
    extra = StrengthSetFactory(workout=planned, set_number=2, weight_kg=70, reps=8, done=False)

    client.post(reverse("set_adjust", args=[row.pk]), {"field": "weight_kg", "dir": "up"})
    client.post(reverse("set_delete", args=[extra.pk]))

    row.refresh_from_db()
    assert row.weight_kg == Decimal("72.50")
    assert planned.sets.count() == 1


def test_draft_set_cannot_be_marked_done(client, user):
    """Подход нельзя выполнить до старта — иначе в записанной тренировке
    оказались бы подходы, которых человек не делал."""
    client.force_login(user)
    planned = draft(user)
    row = StrengthSetFactory(workout=planned, set_number=1, done=False)

    response = client.post(reverse("set_done", args=[row.pk]))

    assert response.status_code == 404
    row.refresh_from_db()
    assert row.done is False


@pytest.mark.parametrize("method", ["get", "post"])
def test_draft_cannot_be_finished(client, user, method):
    client.force_login(user)
    planned = draft(user)
    StrengthSetFactory(workout=planned, set_number=1, done=False)

    response = getattr(client, method)(reverse("workout_finish", args=[planned.pk]))

    assert response.status_code == 404
    planned.refresh_from_db()
    assert planned.duration_min is None
    assert planned.sets.count() == 1


def test_summary_of_draft_leads_back_to_draft_screen(client, user):
    client.force_login(user)
    planned = draft(user)

    response = client.get(reverse("workout_summary", args=[planned.pk]))

    assert response.status_code == 302
    assert response.url == reverse("workout_live", args=[planned.pk])


# ---------- Старт ----------


def test_start_sets_the_clock_and_keeps_the_plan(client, user):
    client.force_login(user)
    bench = ExerciseFactory(name="Жим лёжа")
    past = WorkoutFactory(user=user)
    StrengthSetFactory(workout=past, exercise=bench, set_number=1, weight_kg=80, reps=5)
    planned = draft(user)
    services.create_planned_sets(planned, bench)

    response = client.post(reverse("draft_start", args=[planned.pk]))

    planned.refresh_from_db()
    assert planned.started_at is not None
    assert planned.is_planned is False
    # План не тронут: подставленный вес и повторы на месте, подход не «выполнен».
    assert [(row.weight_kg, row.reps, row.done) for row in planned.sets.all()] == [
        (Decimal("80.00"), 5, False)
    ]
    assert response.url == reverse("workout_live", args=[planned.pk])
    content = client.get(response.url).content.decode()
    assert "тренировка идёт" in content
    assert "Подход выполнен" in content


def test_start_twice_keeps_the_first_start_time(client, user):
    client.force_login(user)
    planned = draft(user)

    client.post(reverse("draft_start", args=[planned.pk]))
    planned.refresh_from_db()
    first_start = planned.started_at
    response = client.post(reverse("draft_start", args=[planned.pk]))

    planned.refresh_from_db()
    assert planned.started_at == first_start
    assert response.url == reverse("workout_live", args=[planned.pk])


def test_start_is_refused_while_another_workout_is_running(client, user):
    client.force_login(user)
    live = WorkoutFactory(user=user, duration_min=None)
    planned = draft(user)

    response = client.post(reverse("draft_start", args=[planned.pk]), follow=True)

    planned.refresh_from_db()
    assert planned.started_at is None
    assert response.redirect_chain[0][0] == reverse("workout_live", args=[live.pk])
    assert "Сначала завершите текущую тренировку." in [
        message.message for message in response.context["messages"]
    ]


def test_start_of_cardio_draft_is_404(client, user):
    client.force_login(user)
    planned = draft(user, sport__category=Sport.Category.CARDIO)

    assert client.post(reverse("draft_start", args=[planned.pk])).status_code == 404


# ---------- Удаление ----------


def test_draft_delete_page_shows_plan_instead_of_date(client, user):
    client.force_login(user)
    planned = draft(user)
    StrengthSetFactory(workout=planned, set_number=1, done=False)

    content = client.get(reverse("workout_delete", args=[planned.pk])).content.decode()

    assert "Удалить черновик?" in content
    assert "1 упражнение" in content


def test_draft_delete_removes_planned_sets(client, user):
    client.force_login(user)
    planned = draft(user)
    StrengthSetFactory(workout=planned, set_number=1, done=False)

    response = client.post(reverse("workout_delete", args=[planned.pk]), follow=True)

    assert Workout.objects.filter(pk=planned.pk).exists() is False
    assert StrengthSet.objects.filter(workout_id=planned.pk).exists() is False
    # В истории черновика не было — возвращаться туда бессмысленно.
    assert response.redirect_chain[0][0] == reverse("dashboard")
    assert "Черновик удалён." in [message.message for message in response.context["messages"]]


# ---------- Изоляция данных ----------


@pytest.mark.parametrize(
    ("url_name", "method"),
    [
        pytest.param("workout_live", "get", id="screen"),
        pytest.param("draft_start", "post", id="start"),
        pytest.param("workout_delete", "get", id="delete-page"),
        pytest.param("workout_delete", "post", id="delete"),
        pytest.param("live_exercises", "post", id="attach"),
        pytest.param("live_set_add", "post", id="add-set"),
    ],
)
def test_foreign_draft_is_untouchable(client, user, other_user, url_name, method):
    client.force_login(user)
    alien = draft(other_user)

    response = getattr(client, method)(reverse(url_name, args=[alien.pk]))

    assert response.status_code == 404
    assert Workout.objects.filter(pk=alien.pk).exists() is True


def test_foreign_drafts_are_absent_from_start_modal(client, user, other_user):
    client.force_login(user)
    draft(other_user, sport=SportFactory(name="Чужая силовая", owner=other_user))

    response = client.get(reverse("workout_start"))

    assert list(response.context["drafts"]) == []
    assert "Чужая силовая" not in response.content.decode()


# ---------- Черновик невидим для истории, дашборда и подстановки ----------


def test_draft_is_absent_from_history_and_filter_chips(client, user):
    client.force_login(user)
    finished = WorkoutFactory(user=user, sport=SportFactory(name="Силовая"))
    planned = draft(user, sport=SportFactory(name="Кроссфит", owner=user))

    response = client.get(reverse("workout_history"))

    assert list(response.context["workouts"]) == [finished]
    # Чип вида спорта черновика тоже не появляется: он вёл бы в пустую ленту.
    assert list(response.context["sports_used"]) == [finished.sport]
    assert "Кроссфит" not in response.content.decode()
    assert Workout.objects.filter(pk=planned.pk).planned().exists()


def test_draft_is_absent_from_dashboard_aggregations(user):
    bench = ExerciseFactory(name="Жим лёжа")
    planned = draft(user)
    StrengthSetFactory(workout=planned, exercise=bench, set_number=1, weight_kg=200, reps=5)

    assert stats.seven_day_summary(user)["count"] == 0
    assert stats.latest_workouts(user) == []
    assert stats.strength_records(user) == []
    assert stats.exercise_progress(user, bench) == []


def test_draft_sets_are_not_a_prefill_source(user):
    """Плановые веса черновика не должны подменять «прошлый раз»."""
    bench = ExerciseFactory(name="Жим лёжа")
    past = WorkoutFactory(user=user)
    StrengthSetFactory(workout=past, exercise=bench, set_number=1, weight_kg=70, reps=10)
    planned = draft(user)
    StrengthSetFactory(
        workout=planned, exercise=bench, set_number=1, weight_kg=200, reps=1, done=False
    )

    assert [(row.weight_kg, row.reps) for row in services.last_sets(user, bench)] == [
        (Decimal("70.00"), 10)
    ]


def test_repeat_is_not_blocked_by_a_draft(client, user):
    """Черновик — не «текущая тренировка», повтору он не мешает."""
    client.force_login(user)
    source = WorkoutFactory(user=user)
    StrengthSetFactory(workout=source, set_number=1)
    draft(user)

    response = client.post(reverse("workout_repeat", args=[source.pk]))

    assert response.status_code == 302
    assert Workout.objects.filter(user=user).live().count() == 1


# ---------- Чузер «+» ----------


def test_start_modal_lists_own_drafts_with_exercise_count(client, user):
    client.force_login(user)
    strength = SportFactory(name="Силовая")
    empty = draft(user, sport=strength)
    filled = draft(user, sport=strength)
    for number, exercise in enumerate(ExerciseFactory.create_batch(2), start=1):
        StrengthSetFactory(workout=filled, exercise=exercise, set_number=number, done=False)

    response = client.get(reverse("workout_start"))
    content = response.content.decode()

    assert [row.pk for row in response.context["drafts"]] == [filled.pk, empty.pk]
    assert "Подготовлено" in content
    assert "2 упражнения" in content
    assert "пусто" in content
    assert reverse("workout_live", args=[filled.pk]) in content
