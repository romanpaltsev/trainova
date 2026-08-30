"""Единица измерения упражнения: планка во времени, подтягивания в повторах.

Единица живёт у упражнения, а у подхода хранится её снимок на момент записи —
поэтому смена единицы не переписывает историю. Неприменимые поля обязаны быть
нулём, и это держит база.
"""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from workouts import services, stats
from workouts.models import Exercise, StrengthSet
from workouts.tests.factories import (
    ExerciseFactory,
    RepsSetFactory,
    StrengthSetFactory,
    TimeSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db

MEASURE = Exercise.Measurement


def active(user):
    return WorkoutFactory(user=user, duration_min=None)


def plank():
    return ExerciseFactory(name="Планка", measurement=MEASURE.TIME)


# ---------- Инварианты базы ----------


@pytest.mark.parametrize(
    ("measurement", "values"),
    [
        pytest.param(MEASURE.WEIGHT_REPS, {"duration_sec": 60}, id="вес-со-временем"),
        pytest.param(MEASURE.REPS, {"weight_kg": 80}, id="повторы-с-весом"),
        pytest.param(MEASURE.TIME, {"reps": 60}, id="время-с-повторами"),
        pytest.param(MEASURE.TIME_WEIGHT, {"reps": 5}, id="время-с-весом-и-повторами"),
    ],
)
def test_fields_must_match_measurement(user, measurement, values):
    """Планка с повторами — не «странные данные», а подход, который соврёт в рекордах."""
    workout = WorkoutFactory(user=user)
    fields = {"weight_kg": 0, "reps": 0, "duration_sec": 0} | values

    with pytest.raises(IntegrityError), transaction.atomic():
        StrengthSet.objects.create(
            workout=workout,
            exercise=ExerciseFactory(),
            set_number=1,
            measurement=measurement,
            **fields,
        )


def test_value_display_speaks_the_unit_of_the_set(user):
    workout = WorkoutFactory(user=user)
    weight_reps = StrengthSetFactory(workout=workout, set_number=1, weight_kg=80, reps=8)
    reps = RepsSetFactory(workout=workout, set_number=2, reps=12)
    time = TimeSetFactory(workout=workout, set_number=3, duration_sec=90)
    time_weight = StrengthSetFactory(
        workout=workout,
        set_number=4,
        measurement=MEASURE.TIME_WEIGHT,
        weight_kg=20,
        reps=0,
        duration_sec=45,
    )

    assert weight_reps.value_display == "80 кг × 8"
    assert reps.value_display == "12 повторов"
    assert time.value_display == "1:30"
    assert time_weight.value_display == "0:45 · 20 кг"


def test_tonnage_of_hold_is_zero(user):
    """Удержание ничего не поднимает — поэтому тоннаж тренировки его не считает."""
    assert TimeSetFactory(workout=WorkoutFactory(user=user), set_number=1).tonnage_kg == 0


# ---------- Снимок единицы ----------


def test_planned_sets_take_the_unit_of_the_exercise(user):
    exercise = plank()
    workout = active(user)

    services.create_planned_sets(workout, exercise)

    row = workout.sets.get()
    assert row.measurement == MEASURE.TIME
    assert (row.weight_kg, row.reps, row.duration_sec) == (0, 0, 0)


def test_changing_the_unit_keeps_recorded_sets_as_they_were(client, user):
    """История остаётся в той единице, в которой её записали."""
    exercise = ExerciseFactory(owner=user, name="Моя планка", measurement=MEASURE.TIME)
    past = WorkoutFactory(user=user)
    old = TimeSetFactory(workout=past, exercise=exercise, set_number=1, duration_sec=90)

    client.force_login(user)
    response = client.post(
        reverse("exercise_measurement", args=[exercise.pk]), {"measurement": MEASURE.REPS}
    )

    assert response.status_code == 200
    old.refresh_from_db()
    assert old.measurement == MEASURE.TIME
    assert old.value_display == "1:30"
    # А новые подходы уже в новой единице.
    exercise.refresh_from_db()
    services.create_planned_sets(active(user), exercise)
    fresh = StrengthSet.objects.filter(exercise=exercise, workout__duration_min__isnull=True).get()
    assert fresh.measurement == MEASURE.REPS


def test_prefill_copies_the_hold_time_from_last_workout(client, user):
    exercise = plank()
    past = WorkoutFactory(user=user)
    TimeSetFactory(workout=past, exercise=exercise, set_number=1, duration_sec=60)
    TimeSetFactory(workout=past, exercise=exercise, set_number=2, duration_sec=75)
    workout = active(user)

    client.force_login(user)
    client.post(reverse("live_exercises", args=[workout.pk]), {"exercise": exercise.pk})

    rows = workout.sets.order_by("set_number")
    assert [row.duration_sec for row in rows] == [60, 75]
    assert {row.measurement for row in rows} == {MEASURE.TIME}


def test_added_set_repeats_the_previous_hold(client, user):
    exercise = plank()
    workout = active(user)
    TimeSetFactory(workout=workout, exercise=exercise, set_number=1, duration_sec=75, done=True)

    client.force_login(user)
    client.post(reverse("live_set_add", args=[workout.pk]), {"exercise": exercise.pk})

    added = workout.sets.order_by("-set_number").first()
    assert (added.measurement, added.duration_sec, added.reps) == (MEASURE.TIME, 75, 0)


# ---------- Ввод в живом режиме ----------


def test_time_stepper_changes_hold_by_fifteen_seconds(client, user):
    workout = active(user)
    row = TimeSetFactory(
        workout=workout, exercise=plank(), set_number=1, duration_sec=60, done=False
    )

    client.force_login(user)
    response = client.post(
        reverse("set_adjust", args=[row.pk]), {"field": "duration_sec", "dir": "up"}
    )

    row.refresh_from_db()
    assert row.duration_sec == 75
    assert response.content.decode() == "1:15"


def test_time_does_not_go_below_zero(client, user):
    workout = active(user)
    row = TimeSetFactory(
        workout=workout, exercise=plank(), set_number=1, duration_sec=0, done=False
    )

    client.force_login(user)
    client.post(reverse("set_adjust", args=[row.pk]), {"field": "duration_sec", "dir": "down"})

    row.refresh_from_db()
    assert row.duration_sec == 0


def test_field_from_another_unit_is_rejected(client, user):
    """Вес у планки писать нельзя: иначе подход упёрся бы в ограничение БД."""
    workout = active(user)
    row = TimeSetFactory(workout=workout, exercise=plank(), set_number=1, done=False)

    client.force_login(user)
    response = client.post(
        reverse("set_adjust", args=[row.pk]), {"field": "weight_kg", "dir": "up"}
    )

    assert response.status_code == 400
    row.refresh_from_db()
    assert row.weight_kg == 0


def test_hold_requires_time_not_reps(client, user):
    workout = active(user)
    row = TimeSetFactory(
        workout=workout, exercise=plank(), set_number=1, duration_sec=0, done=False
    )

    client.force_login(user)
    content = client.post(reverse("set_done", args=[row.pk])).content.decode()

    row.refresh_from_db()
    assert row.done is False
    assert "Укажите время." in content


def test_bodyweight_set_can_be_done_without_weight(client, user):
    """Вес 0 — это «со своим весом», а не незаполненный подход."""
    workout = active(user)
    row = StrengthSetFactory(workout=workout, set_number=1, weight_kg=0, reps=10, done=False)

    client.force_login(user)
    client.post(reverse("set_done", args=[row.pk]))

    row.refresh_from_db()
    assert row.done is True


def test_live_screen_shows_one_field_for_a_hold(client, user):
    workout = active(user)
    services.create_planned_sets(workout, plank())

    client.force_login(user)
    content = client.get(reverse("workout_live", args=[workout.pk])).content.decode()

    assert 'id="cs-duration_sec"' in content
    assert 'id="cs-weight_kg"' not in content
    assert "is-single" in content
    assert "Время" in content
    assert "шаг 15 с" in content


# ---------- Подсказки ----------


def test_hints_speak_the_unit(user):
    exercise = plank()
    past = WorkoutFactory(user=user)
    sets = [
        TimeSetFactory(workout=past, exercise=exercise, set_number=1, duration_sec=60),
        TimeSetFactory(workout=past, exercise=exercise, set_number=2, duration_sec=90),
    ]

    assert services.last_time_hint(sets) == "прошлый раз: 1:00 · 1:30"
    assert services.done_hint(sets) == "2 подхода · 2:30"


def test_queue_hint_of_an_exercise_without_weight_shows_its_history(user):
    """Раньше подсказка очереди смотрела на вес и у планки всегда врала
    «первое выполнение» — даже с полной историей."""
    workout = active(user)
    exercise = plank()
    planned = [
        TimeSetFactory(
            workout=workout, exercise=exercise, set_number=1, duration_sec=90, done=False
        )
    ]

    assert services.queue_hint(planned) == "прошлый раз: 1 подход · до 1:30"


def test_reps_hints_count_repetitions(user):
    pullups = ExerciseFactory(name="Подтягивания", measurement=MEASURE.REPS)
    past = WorkoutFactory(user=user)
    sets = [
        RepsSetFactory(workout=past, exercise=pullups, set_number=1, reps=12),
        RepsSetFactory(workout=past, exercise=pullups, set_number=2, reps=10),
    ]

    assert services.last_time_hint(sets) == "прошлый раз: 12 · 10"
    assert services.done_hint(sets) == "2 подхода · 22 повтора"


# ---------- Итог тренировки ----------


@pytest.mark.parametrize(
    ("kind", "label", "value"),
    [
        pytest.param("weight", "тоннаж", "640 кг", id="весовая"),
        pytest.param("reps", "повторы", "12 повторов", id="повторная"),
        pytest.param("time", "удержание", "1:00", id="удержание"),
    ],
)
def test_workload_metric_follows_the_content(client, user, kind, label, value):
    workout = WorkoutFactory(user=user)
    if kind == "weight":
        StrengthSetFactory(workout=workout, set_number=1, weight_kg=80, reps=8)
    elif kind == "reps":
        RepsSetFactory(workout=workout, set_number=1, reps=12)
    else:
        TimeSetFactory(workout=workout, set_number=1, duration_sec=60)

    client.force_login(user)
    content = client.get(reverse("workout_summary", args=[workout.pk])).content.decode()

    assert f"<dt>{label}</dt>" in content
    assert value in content


def test_mixed_workout_shows_tonnage(client, user):
    """Если есть весовая работа, она и есть главная метрика тренировки."""
    workout = WorkoutFactory(user=user)
    StrengthSetFactory(workout=workout, set_number=1, weight_kg=80, reps=8)
    TimeSetFactory(workout=workout, exercise=plank(), set_number=1, duration_sec=60)

    client.force_login(user)
    content = client.get(reverse("workout_history")).content.decode()

    assert "<dt>тоннаж</dt>" in content
    assert "640 кг" in content


# ---------- Страница упражнения и каталог ----------


def test_exercise_page_charts_the_hold(client, user):
    exercise = plank()
    for seconds in (60, 90):
        workout = WorkoutFactory(user=user)
        TimeSetFactory(workout=workout, exercise=exercise, set_number=1, duration_sec=seconds)

    client.force_login(user)
    response = client.get(reverse("exercise_detail", args=[exercise.pk]))
    content = response.content.decode()

    assert response.context["chart"]["format"] == "time"
    assert response.context["chart"]["unit"] == ""
    assert response.context["chart"]["values"] == [60.0, 90.0]
    assert "Максимум: удержание" in content
    assert "рекорд 1:30" in content
    assert "до 1:30" in content


def test_catalog_shows_record_in_the_right_unit(client, user):
    exercise = plank()
    TimeSetFactory(
        workout=WorkoutFactory(user=user), exercise=exercise, set_number=1, duration_sec=90
    )

    client.force_login(user)
    content = client.get(reverse("exercise_list")).content.decode()

    assert "1:30" in content
    assert "1:30 кг" not in content
    # Непривычная единица подписана прямо в строке каталога.
    assert "время" in content


def test_dashboard_record_names_the_metric(client, user):
    exercise = plank()
    TimeSetFactory(
        workout=WorkoutFactory(user=user), exercise=exercise, set_number=1, duration_sec=90
    )

    client.force_login(user)
    content = client.get(reverse("dashboard")).content.decode()

    assert "1:30" in content
    assert "удержание" in content


# ---------- Смена единицы: доступ ----------


def test_own_exercise_measurement_is_editable(client, user):
    exercise = ExerciseFactory(owner=user, measurement=MEASURE.WEIGHT_REPS)

    client.force_login(user)
    response = client.get(reverse("exercise_detail", args=[exercise.pk]))

    assert response.context["can_edit_measurement"] is True
    assert reverse("exercise_measurement", args=[exercise.pk]) in response.content.decode()


def test_global_exercise_measurement_is_shown_but_not_editable(client, user):
    """Глобальные справочники правит только админ — страница видна, чипов нет."""
    exercise = ExerciseFactory(owner=None, measurement=MEASURE.TIME)

    client.force_login(user)
    page = client.get(reverse("exercise_detail", args=[exercise.pk]))
    saved = client.post(
        reverse("exercise_measurement", args=[exercise.pk]), {"measurement": MEASURE.REPS}
    )

    assert page.context["can_edit_measurement"] is False
    assert "время" in page.content.decode()
    assert reverse("exercise_measurement", args=[exercise.pk]) not in page.content.decode()
    assert saved.status_code == 404
    exercise.refresh_from_db()
    assert exercise.measurement == MEASURE.TIME


def test_foreign_exercise_is_invisible_entirely(client, user, other_user):
    """Священное правило: чужое личное упражнение недоступно и по прямому URL."""
    exercise = ExerciseFactory(owner=other_user)

    client.force_login(user)

    assert client.get(reverse("exercise_detail", args=[exercise.pk])).status_code == 404
    assert (
        client.post(
            reverse("exercise_measurement", args=[exercise.pk]), {"measurement": MEASURE.TIME}
        ).status_code
        == 404
    )
    exercise.refresh_from_db()
    assert exercise.measurement == MEASURE.WEIGHT_REPS


def test_unknown_measurement_is_rejected(client, user):
    exercise = ExerciseFactory(owner=user)

    client.force_login(user)
    response = client.post(
        reverse("exercise_measurement", args=[exercise.pk]), {"measurement": "стоны"}
    )

    assert response.status_code == 400
    exercise.refresh_from_db()
    assert exercise.measurement == MEASURE.WEIGHT_REPS


# ---------- Создание упражнения ----------


def test_quick_create_takes_the_chosen_unit(client, user):
    workout = active(user)

    client.force_login(user)
    client.post(
        reverse("live_exercises", args=[workout.pk]),
        {"name": "Планка боком", "measurement": MEASURE.TIME},
    )

    created = Exercise.objects.get(name="Планка боком")
    assert created.owner == user
    assert created.measurement == MEASURE.TIME
    assert workout.sets.get().measurement == MEASURE.TIME


def test_quick_create_of_existing_name_keeps_its_unit(client, user):
    """Ввод названия существующего упражнения добавляет его, а не переопределяет:
    единицу глобального упражнения так подменить нельзя."""
    existing = plank()
    workout = active(user)

    client.force_login(user)
    client.post(
        reverse("live_exercises", args=[workout.pk]),
        {"name": "Планка", "measurement": MEASURE.WEIGHT_REPS},
    )

    existing.refresh_from_db()
    assert existing.measurement == MEASURE.TIME
    assert Exercise.objects.filter(name__iexact="Планка").count() == 1
    assert workout.sets.get().measurement == MEASURE.TIME


def test_search_keeps_the_chosen_unit(client, user):
    """Чипы живут в свапаемом блоке результатов, поэтому выбор ездит на сервер:
    иначе следующая набранная буква сбрасывала бы его на «вес × повторы»."""
    workout = active(user)

    client.force_login(user)
    response = client.get(
        reverse("live_exercises", args=[workout.pk]),
        {"q": "Планка боком", "measurement": MEASURE.TIME},
    )

    assert response.context["selected_measurement"] == MEASURE.TIME
    assert 'id="new-measure-time"' in response.content.decode()


def test_search_falls_back_to_the_usual_unit(client, user):
    workout = active(user)

    client.force_login(user)
    response = client.get(
        reverse("live_exercises", args=[workout.pk]), {"q": "Планка боком", "measurement": "стоны"}
    )

    assert response.context["selected_measurement"] == MEASURE.WEIGHT_REPS


def test_seed_measures_plank_in_time(db):
    from django.core.management import call_command

    call_command("seed")

    assert Exercise.objects.get(name="Планка").measurement == MEASURE.TIME
    assert Exercise.objects.get(name="Подтягивания").measurement == MEASURE.WEIGHT_REPS


def test_records_query_stays_single(user, django_assert_max_num_queries):
    """Рекорд на каждую единицу считается одним запросом, а не по одному на тип."""
    for measurement, factory in (
        (MEASURE.TIME, TimeSetFactory),
        (MEASURE.REPS, RepsSetFactory),
    ):
        exercise = ExerciseFactory(measurement=measurement)
        factory(workout=WorkoutFactory(user=user), exercise=exercise, set_number=1)
    StrengthSetFactory(workout=WorkoutFactory(user=user), set_number=1, weight_kg=90, reps=5)

    with django_assert_max_num_queries(1):
        records = stats.strength_records(user)

    assert len(records) == 3
    assert [record["metric_label"] for record in records] == ["вес", "удержание", "повторы"]


def test_progress_of_reps_exercise_counts_repetitions(user):
    pullups = ExerciseFactory(name="Подтягивания", measurement=MEASURE.REPS)
    workout = WorkoutFactory(user=user)
    RepsSetFactory(workout=workout, exercise=pullups, set_number=1, reps=8)
    RepsSetFactory(workout=workout, exercise=pullups, set_number=2, reps=12)

    progress = stats.exercise_progress(user, pullups)

    assert progress[0]["max_value"] == 12.0
    assert progress[0]["max_value_display"] == "12 повторов"


def test_decimal_weight_survives_time_weight_display(user):
    row = StrengthSetFactory(
        workout=WorkoutFactory(user=user),
        set_number=1,
        measurement=MEASURE.TIME_WEIGHT,
        weight_kg=Decimal("22.5"),
        reps=0,
        duration_sec=120,
    )

    assert row.value_display == "2:00 · 22,5 кг"
