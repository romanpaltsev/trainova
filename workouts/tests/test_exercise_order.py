"""Порядок упражнений в тренировке — фактический, а не плановый.

До появления `StrengthSet.done_at` порядок восстанавливался по id подходов, то
есть по порядку добавления упражнения. Для тренировки, которую набивают в зале по
ходу, это совпадало с фактом; для черновика, подготовленного заранее и
выполненного не по плану, — нет. Здесь проверяется новое правило
(`models.exercise_order_key`) и то, что старые тренировки без меток ведут себя
как раньше.
"""

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from workouts import services
from workouts.models import StrengthSet
from workouts.tests.factories import ExerciseFactory, StrengthSetFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


def planned_set(workout, exercise, number):
    """Плановый подход живого режима: как их создаёт «+ Упражнение»."""
    return StrengthSetFactory(
        workout=workout, exercise=exercise, set_number=number, weight_kg=70, reps=8, done=False
    )


def mark_done(client, row):
    return client.post(reverse("set_done", args=[row.pk]))


def order(workout):
    return [group["exercise"].name for group in services.exercise_groups(workout)]


def positions(workout):
    return [group["position"] for group in services.exercise_groups(workout)]


# ---------- Главное правило ----------


def test_group_order_follows_execution_not_plan(client, user):
    """Черновик подготовлен A, B, C, а выполнен C, B, A — итог показывает факт."""
    workout = WorkoutFactory(user=user, duration_min=None)
    rows = {}
    for number, name in enumerate(("A", "B", "C"), start=1):
        rows[name] = planned_set(workout, ExerciseFactory(name=name), number)

    client.force_login(user)
    for name in ("C", "B", "A"):
        mark_done(client, rows[name])

    assert order(workout) == ["C", "B", "A"]
    assert positions(workout) == [1, 2, 3]


def test_workout_without_marks_keeps_addition_order(user):
    """Старая тренировка: меток нет, порядок тот же, что показывался раньше."""
    workout = WorkoutFactory(user=user, duration_min=60)
    squat = ExerciseFactory(name="Присед со штангой")
    bench = ExerciseFactory(name="Жим лёжа")
    # Присед добавлен первым: порядок не алфавитный и не по имени.
    StrengthSetFactory(workout=workout, exercise=squat, set_number=1, weight_kg=100, reps=5)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1, weight_kg=70, reps=10)

    assert order(workout) == ["Присед со штангой", "Жим лёжа"]
    assert positions(workout) == [1, 2]


def test_marked_exercises_go_before_untouched(client, user):
    """Начатое упражнение занимает своё фактическое место, нетронутые — план."""
    workout = WorkoutFactory(user=user, duration_min=None)
    first = planned_set(workout, ExerciseFactory(name="A"), 1)
    planned_set(workout, ExerciseFactory(name="B"), 2)
    third = planned_set(workout, ExerciseFactory(name="C"), 3)

    client.force_login(user)
    mark_done(client, third)
    mark_done(client, first)

    # C сделали первым, A вторым, B ещё не трогали — он остаётся в хвосте.
    assert order(workout) == ["C", "A", "B"]


# ---------- Отмена подхода ----------


def test_undo_of_last_done_set_returns_exercise_to_plan_order(client, user):
    """У упражнения не осталось выполненных подходов — оно возвращается в план."""
    workout = WorkoutFactory(user=user, duration_min=None)
    planned_set(workout, ExerciseFactory(name="A"), 1)
    second = planned_set(workout, ExerciseFactory(name="B"), 2)

    client.force_login(user)
    mark_done(client, second)
    assert order(workout) == ["B", "A"]

    client.post(reverse("set_undo", args=[second.pk]))

    assert order(workout) == ["A", "B"]
    second.refresh_from_db()
    assert second.done_at is None


def test_partly_done_exercise_keeps_place_when_one_set_is_undone(client, user):
    """Отмена одного подхода из нескольких место упражнения не меняет."""
    workout = WorkoutFactory(user=user, duration_min=None)
    first_exercise = ExerciseFactory(name="A")
    first = planned_set(workout, first_exercise, 1)
    second = planned_set(workout, first_exercise, 2)
    other = planned_set(workout, ExerciseFactory(name="B"), 3)

    client.force_login(user)
    mark_done(client, first)
    mark_done(client, second)
    mark_done(client, other)
    assert order(workout) == ["A", "B"]

    client.post(reverse("set_undo", args=[first.pk]))

    # Метка второго подхода всё равно раньше метки B.
    assert order(workout) == ["A", "B"]


# ---------- Сквозная нумерация живого экрана ----------


def test_numbers_are_one_sequence_across_live_sections(client, user):
    """Выполненное, текущее и очередь нумеруются одной последовательностью."""
    workout = WorkoutFactory(user=user, duration_min=None)
    done_row = planned_set(workout, ExerciseFactory(name="A"), 1)
    planned_set(workout, ExerciseFactory(name="B"), 2)
    planned_set(workout, ExerciseFactory(name="C"), 3)

    client.force_login(user)
    mark_done(client, done_row)

    context = services.live_context(workout)

    assert context["done_groups"][0]["position"] == 1
    assert context["current_group"]["position"] == 2
    assert context["queue_groups"][0]["position"] == 3


def test_started_exercise_number_stays_before_fully_done_one(client, user):
    """Номера в разделах могут идти не подряд — они про факт, а не про раздел."""
    workout = WorkoutFactory(user=user, duration_min=None)
    started = ExerciseFactory(name="A")
    first = planned_set(workout, started, 1)
    planned_set(workout, started, 2)
    other = planned_set(workout, ExerciseFactory(name="B"), 3)

    client.force_login(user)
    mark_done(client, first)
    mark_done(client, other)
    # Возвращаем текущим то, что начали раньше.
    client.post(reverse("live_exercise_select", args=[workout.pk]), {"exercise": started.pk})

    context = services.live_context(workout)

    assert context["current_group"]["position"] == 1
    assert context["done_groups"][0]["position"] == 2


# ---------- Завершение тренировки ----------


def test_summary_order_survives_deleting_planned_sets(client, user):
    """Регресс: раньше упражнение съезжало вниз, если его плановые строки удалились.

    У X все плановые подходы остаются невыполненными, а выполняется тот, что
    добавлен кнопкой «+ Добавить подход» — то есть с самым большим id. После
    завершения плановые удаляются, и старое правило (min(id)) ставило X под Y.
    """
    workout = WorkoutFactory(user=user, started_at=timezone.now(), duration_min=None)
    x = ExerciseFactory(name="X")
    y = ExerciseFactory(name="Y")
    for number in (1, 2, 3):
        planned_set(workout, x, number)
    y_row = planned_set(workout, y, 1)
    extra_x = planned_set(workout, x, 4)

    client.force_login(user)
    mark_done(client, extra_x)
    mark_done(client, y_row)
    client.post(reverse("workout_finish", args=[workout.pk]))

    workout.refresh_from_db()
    assert workout.is_finished
    assert order(workout) == ["X", "Y"]


# ---------- Инвариант в базе ----------


def test_done_at_without_done_is_rejected(user):
    """Метка бывает только у выполненного подхода — это держит констрейнт."""
    workout = WorkoutFactory(user=user, duration_min=None)
    exercise = ExerciseFactory(name="A")

    with pytest.raises(IntegrityError), transaction.atomic():
        StrengthSet.objects.create(
            workout=workout,
            exercise=exercise,
            set_number=1,
            weight_kg=70,
            reps=8,
            done=False,
            done_at=timezone.now(),
        )


# ---------- Изоляция ----------


def test_order_ignores_other_users_sets(user, other_user):
    """Чужие подходы того же глобального упражнения на порядок не влияют."""
    exercise = ExerciseFactory(name="Жим лёжа", owner=None)
    mine = WorkoutFactory(user=user, duration_min=60)
    StrengthSetFactory(workout=mine, exercise=exercise, set_number=1, weight_kg=80, reps=8)
    alien = WorkoutFactory(user=other_user, duration_min=60)
    StrengthSetFactory(
        workout=alien, exercise=exercise, set_number=1, weight_kg=200, reps=1, done_at=None
    )

    groups = services.exercise_groups(mine)

    assert len(groups) == 1
    assert [row.pk for row in groups[0]["sets"]] == list(
        mine.sets.values_list("pk", flat=True).order_by("pk")
    )
