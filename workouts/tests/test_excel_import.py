"""Загрузка истории из книги Excel: разбор строк, справочники, дубли, изоляция."""

import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.tests.factories import UserFactory
from workouts import excel, excel_import
from workouts.models import (
    CardioDetails,
    Exercise,
    ExerciseNote,
    Location,
    Sport,
    StrengthSet,
    Workout,
)
from workouts.tests.factories import (
    ExerciseFactory,
    LocationFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)
from workouts.tests.sheets import build_sheet, dump

pytestmark = pytest.mark.django_db

PAST = date(2026, 9, 4)


def strength_row(**extra):
    """Строка силового подхода со всем обязательным."""
    return {
        "date": PAST,
        "sport": "Силовая",
        "exercise": "Жим лёжа",
        "weight": 80,
        "reps": 8,
        "duration": 60,
    } | extra


def cardio_row(**extra):
    return {
        "date": PAST,
        "sport": "Велосипед",
        "distance": 24.5,
        "pulse": 142,
        "duration": 60,
    } | extra


def run(user, rows, **kwargs):
    return excel_import.import_workbook(user, build_sheet(rows, **kwargs))


def test_workbook_creates_workout_with_sets(user):
    report = run(user, [strength_row(weight=80, reps=8), strength_row(weight=82.5, reps=6)])

    workout = Workout.objects.get(user=user)
    assert report.workouts == 1
    assert report.sets == 2
    assert workout.duration_min == 60
    assert timezone.localtime(workout.started_at).date() == PAST
    assert [(row.weight_kg, row.reps) for row in workout.sets.order_by("set_number")] == [
        (Decimal("80.00"), 8),
        (Decimal("82.50"), 6),
    ]


def test_missing_time_means_noon_for_past_day(user):
    run(user, [strength_row()])

    started = timezone.localtime(Workout.objects.get(user=user).started_at)

    # Пустое время — то же правило, что у записи задним числом: полдень.
    assert (started.hour, started.minute) == (12, 0)


def test_start_time_from_file_is_used(user):
    run(user, [strength_row(start=time(19, 45))])

    started = timezone.localtime(Workout.objects.get(user=user).started_at)

    assert (started.hour, started.minute) == (19, 45)


def test_set_numbers_are_renumbered(user):
    """Номер из файла — подсказка человеку, а не данные: пропуски не переносятся."""
    run(user, [strength_row(set_number=5), strength_row(set_number=7)])

    numbers = list(
        Workout.objects.get(user=user)
        .sets.order_by("set_number")
        .values_list("set_number", flat=True)
    )

    assert numbers == [1, 2]


def test_sets_are_done_without_timestamp(user):
    run(user, [strength_row()])

    row = StrengthSet.objects.get()

    assert row.done is True
    # Времени подхода в таблице нет, и NULL у выполненного как раз это и значит.
    assert row.done_at is None


@pytest.mark.parametrize(
    ("cells", "measurement", "expected"),
    [
        ({"weight": 80, "reps": 8}, Exercise.Measurement.WEIGHT_REPS, (Decimal("80.00"), 8, 0)),
        ({"reps": 12}, Exercise.Measurement.WEIGHT_REPS, (Decimal("0.00"), 12, 0)),
        ({"hold": "1:30"}, Exercise.Measurement.TIME, (Decimal("0.00"), 0, 90)),
        (
            {"hold": "1:30", "weight": 20},
            Exercise.Measurement.TIME_WEIGHT,
            (Decimal("20.00"), 0, 90),
        ),
    ],
)
def test_new_exercise_measurement_is_guessed(user, cells, measurement, expected):
    """Единица нового упражнения выводится из заполненных колонок."""
    run(user, [strength_row(weight=None, reps=None) | cells])

    exercise = Exercise.objects.get(owner=user)
    row = StrengthSet.objects.get()

    assert exercise.measurement == measurement
    assert (row.weight_kg, row.reps, row.duration_sec) == expected
    assert row.measurement == measurement


def test_existing_exercise_keeps_its_measurement(user):
    """Единицу существующего упражнения файл не переписывает, лишнее обнуляется."""
    ExerciseFactory(name="Планка", measurement=Exercise.Measurement.TIME)

    run(user, [strength_row(exercise="Планка", weight=50, reps=10, hold="1:00")])

    row = StrengthSet.objects.get()
    assert row.measurement == Exercise.Measurement.TIME
    assert (row.weight_kg, row.reps, row.duration_sec) == (Decimal("0.00"), 0, 60)


def test_cardio_row_creates_details(user):
    SportFactory(name="Велосипед", category=Sport.Category.CARDIO)

    report = run(user, [cardio_row()])

    details = CardioDetails.objects.get()
    assert report.workouts == 1
    assert report.sets == 0
    assert details.distance_km == Decimal("24.50")
    assert details.avg_heart_rate == 142


def test_cardio_without_distance_is_still_a_workout(user):
    SportFactory(name="Плавание", category=Sport.Category.CARDIO)

    run(user, [cardio_row(sport="Плавание", distance=None, pulse=None, duration=40)])

    assert Workout.objects.get(user=user).duration_min == 40
    assert not CardioDetails.objects.exists()


def test_unknown_sport_is_created_as_personal(user):
    report = run(user, [strength_row(sport="Кроссфит")])

    sport = Sport.objects.get(name="Кроссфит")
    assert sport.owner == user
    assert report.created_sports == ["Кроссфит"]


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (strength_row(sport="Кроссфит"), Sport.Category.STRENGTH),
        (cardio_row(sport="Гребля"), Sport.Category.CARDIO),
        ({"date": PAST, "sport": "Гребля", "duration": 40}, Sport.Category.CARDIO),
    ],
)
def test_new_sport_category_is_guessed(user, row, expected):
    run(user, [row])

    assert Sport.objects.get(owner=user).category == expected


def test_unknown_location_is_created_and_becomes_default(user):
    run(user, [strength_row(location="СпортЛайф")])

    place = Location.objects.get(owner=user)
    assert place.name == "СпортЛайф"
    # Первое место сразу дефолтное — правило location_for_name, общее с формами.
    assert place.is_default is True
    assert Workout.objects.get(user=user).location == place


def test_names_are_matched_case_insensitively(user):
    ExerciseFactory(name="Жим лёжа")
    LocationFactory(owner=user, name="СпортЛайф")

    report = run(user, [strength_row(exercise="жим лёжа", location="спортлайф")])

    assert Exercise.objects.filter(name__iexact="жим лёжа").count() == 1
    assert Location.objects.filter(owner=user).count() == 1
    assert report.created_exercises == []


def test_duplicate_by_date_and_sport_is_skipped(user):
    sport = SportFactory(name="Силовая")
    existing = WorkoutFactory(
        user=user,
        sport=sport,
        started_at=timezone.make_aware(datetime.combine(PAST, time(9, 0))),
    )
    StrengthSetFactory(workout=existing)

    report = run(user, [strength_row()])

    assert report.workouts == 0
    assert report.skipped == 1
    assert Workout.objects.filter(user=user).count() == 1


def test_second_import_of_same_file_changes_nothing(user):
    rows = [strength_row(), cardio_row()]
    SportFactory(name="Велосипед", category=Sport.Category.CARDIO)
    run(user, rows)
    before = dump(user)

    report = run(user, rows)

    assert report.workouts == 0
    assert report.skipped == 2
    assert dump(user) == before


def test_duplicate_inside_one_file_is_skipped(user):
    """Две одинаковые тренировки в одном файле — вторая уходит в «пропущено»."""
    report = run(user, [strength_row(start=time(9, 0)), strength_row(start=time(19, 0))])

    assert report.workouts == 1
    assert report.skipped == 1


def test_two_sports_on_one_day_are_two_workouts(user):
    SportFactory(name="Велосипед", category=Sport.Category.CARDIO)

    report = run(user, [strength_row(), cardio_row()])

    assert report.workouts == 2
    assert Workout.objects.filter(user=user).count() == 2


def test_broken_row_does_not_block_the_rest(user):
    report = run(
        user,
        [
            strength_row(weight="ой"),
            strength_row(date=date(2026, 9, 5), weight=100, reps=5),
        ],
    )

    assert report.workouts == 2
    assert report.error_count == 1
    assert "Строка 2" in report.errors[0]


def test_workout_without_duration_is_reported(user):
    report = run(user, [strength_row(duration=None)])

    assert report.workouts == 0
    assert report.error_count == 1
    assert "длительность" in report.errors[0]
    assert not Workout.objects.exists()


def test_future_date_is_rejected(user):
    tomorrow = timezone.localdate() + timedelta(days=1)

    report = run(user, [strength_row(date=tomorrow)])

    assert report.workouts == 0
    assert "будущем" in report.errors[0]


def test_empty_set_is_reported(user):
    """Подход без повторов — потерянная строка тетрадки, а не пустое место."""
    report = run(user, [strength_row(reps=None), strength_row(weight=90, reps=5)])

    assert report.sets == 1
    assert report.error_count == 1
    assert "повтор" in report.errors[0].lower()


def test_no_live_workouts_after_import(user):
    run(user, [strength_row(), strength_row(date=date(2026, 9, 5))])

    # Главный инвариант: импорт не создаёт «идущую» тренировку — она упёрлась бы
    # в unique_live_workout_per_user и повисла бы в интерфейсе.
    assert not Workout.objects.live().exists()
    assert not Workout.objects.planned().exists()


def test_strength_workout_without_sets_is_not_created(user):
    SportFactory(name="Силовая")

    report = run(user, [{"date": PAST, "sport": "Силовая", "duration": 60, "exercise": None}])

    assert report.workouts == 0
    assert not Workout.objects.exists()


def test_not_a_workbook_gives_human_error(user):
    with pytest.raises(excel.WorkbookError) as error:
        excel_import.import_workbook(user, io.BytesIO(b"nothing like a workbook"))

    assert ".xlsx" in str(error.value)


def test_missing_required_columns_gives_human_error(user):
    with pytest.raises(excel.WorkbookError) as error:
        run(user, [], header=["Когда", "Что делал"])

    assert "Дата" in str(error.value)


def test_empty_workbook_is_not_an_error(user):
    report = run(user, [])

    assert report.workouts == 0
    assert report.error_count == 0


def test_too_many_rows_are_rejected(user, monkeypatch):
    monkeypatch.setattr(excel, "MAX_ROWS", 1)

    with pytest.raises(excel.WorkbookError) as error:
        run(user, [strength_row(), strength_row(date=date(2026, 9, 5))])

    assert "строк" in str(error.value)


def test_import_does_not_touch_other_users_data(user):
    stranger = UserFactory()
    theirs = WorkoutFactory(user=stranger, started_at=timezone.now() - timedelta(days=30))
    StrengthSetFactory(workout=theirs)
    before = dump(stranger)

    run(user, [strength_row(exercise="Новое упражнение", location="Новое место")])

    assert dump(stranger) == before
    assert Exercise.objects.get(name="Новое упражнение").owner == user
    assert Location.objects.get(name="Новое место").owner == user
    assert Workout.objects.filter(user=stranger).count() == 1


def test_note_columns_are_imported(user):
    run(
        user,
        [
            strength_row(workout_note="Тяжело зашло", exercise_note="Узкий хват"),
            strength_row(weight=85, reps=5),
        ],
    )

    workout = Workout.objects.get(user=user)
    assert workout.note == "Тяжело зашло"
    assert ExerciseNote.objects.get(workout=workout).text == "Узкий хват"


def test_round_trip_keeps_the_diary(user):
    """Главный тест: выгрузка → загрузка в чистый аккаунт → те же данные."""
    twin = UserFactory()
    place = LocationFactory(owner=user, name="СпортЛайф")
    bench = ExerciseFactory(name="Жим лёжа")
    plank = ExerciseFactory(name="Планка", measurement=Exercise.Measurement.TIME)
    pullups = ExerciseFactory(name="Подтягивания")
    strength = SportFactory(name="Силовая")
    bike = SportFactory(name="Велосипед", category=Sport.Category.CARDIO)

    workout = WorkoutFactory(
        user=user,
        sport=strength,
        location=place,
        started_at=timezone.make_aware(datetime.combine(PAST, time(18, 30))),
        duration_min=89,
        note="Тяжело зашло",
    )
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1, weight_kg=80, reps=8)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=2, weight_kg=82.5, reps=6)
    StrengthSetFactory(
        workout=workout,
        exercise=plank,
        set_number=1,
        measurement=Exercise.Measurement.TIME,
        weight_kg=0,
        reps=0,
        duration_sec=90,
    )
    StrengthSetFactory(
        workout=workout, exercise=pullups, set_number=1, weight_kg=0, reps=12, duration_sec=0
    )
    ExerciseNote.objects.create(workout=workout, exercise=bench, text="Узкий хват")

    ride = WorkoutFactory(
        user=user,
        sport=bike,
        started_at=timezone.make_aware(datetime.combine(date(2026, 9, 3), time(8, 0))),
        duration_min=60,
    )
    CardioDetails.objects.create(workout=ride, distance_km=Decimal("24.50"), avg_heart_rate=142)

    book = excel.build_workbook(user)
    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)

    report = excel_import.import_workbook(twin, buffer)

    assert report.workouts == 2
    assert report.error_count == 0
    assert dump(twin) == dump(user)
