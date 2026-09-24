"""Загрузка истории из книги Excel: строки листа → тренировки в базе.

Разбор ячеек живёт в excel.read_sheet, здесь — только смысл: как строки
складываются в тренировки, что делать с незнакомыми справочниками и какие
записи считать уже существующими.

Битая строка не отменяет весь файл: из-за одной опечатки в трёхсотстрочной
таблице терять остальное незачем. Каждая тренировка пишется своим savepoint'ом,
а исправленный файл можно загрузить повторно — дубли отсеются.
"""

from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone

from workouts import excel, services
from workouts.models import (
    NOTE_MAX_LENGTH,
    REQUIRED_FIELD,
    CardioDetails,
    Exercise,
    ExerciseNote,
    Location,
    Sport,
    StrengthSet,
    Workout,
    collapse_spaces,
)

# Файл целиком держим в памяти, поэтому лимит скромный. Он же ниже, чем
# client_max_body_size 4m у nginx: так человек увидит наше сообщение, а не
# страницу 413 от прокси.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
# Сообщений об ошибках храним первые двести, считаем все: список на три тысячи
# строк читать всё равно никто не станет.
ERRORS_KEPT = 200
# Та же граница, что у форм (MAX_DURATION_HOURS): тренировка в 30 часов — опечатка.
MAX_DURATION_MIN = 24 * 60


@dataclass
class ImportReport:
    """Что получилось: числа для итога и списки для разбора полётов."""

    workouts: int = 0
    sets: int = 0
    skipped: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    created_sports: list[str] = field(default_factory=list)
    created_exercises: list[str] = field(default_factory=list)
    created_locations: list[str] = field(default_factory=list)

    @property
    def created_anything(self):
        return bool(self.created_sports or self.created_exercises or self.created_locations)

    @property
    def hidden_errors(self):
        return max(0, self.error_count - len(self.errors))

    def add_error(self, number, message):
        self.error_count += 1
        if len(self.errors) < ERRORS_KEPT:
            self.errors.append(f"Строка {number}: {message}")

    def add_warning(self, message):
        if message not in self.warnings and len(self.warnings) < ERRORS_KEPT:
            self.warnings.append(message)


class Catalog:
    """Справочники пользователя: словарь в памяти плюс автосоздание личных записей.

    Поиск по словарю, а не запросом на строку: иначе число запросов росло бы
    вместе с числом строк файла. Создание идёт через services — те же функции,
    что у живого режима, поэтому правило «совпало имя — это оно» одно на всех.
    """

    def __init__(self, user, report):
        self.user = user
        self.report = report
        self.sports = {
            sport.name.lower(): sport for sport in Sport.objects.visible_to(user).order_by("pk")
        }
        self.exercises = {
            item.name.lower(): item for item in Exercise.objects.visible_to(user).order_by("pk")
        }
        self.locations = {
            place.name.lower(): place for place in Location.objects.filter(owner=user)
        }

    def sport(self, name, *, category):
        key = collapse_spaces(name).lower()
        if key not in self.sports:
            self.sports[key] = services.sport_for_name(self.user, name, category=category)
            self.report.created_sports.append(self.sports[key].name)
        return self.sports[key]

    def exercise(self, name, *, measurement):
        key = collapse_spaces(name).lower()
        if key not in self.exercises:
            self.exercises[key] = services.exercise_for_name(
                self.user, name, measurement=measurement
            )
            self.report.created_exercises.append(self.exercises[key].name)
        return self.exercises[key]

    def location(self, name):
        key = collapse_spaces(name).lower()
        if key not in self.locations:
            self.locations[key] = services.location_for_name(self.user, name)
            self.report.created_locations.append(self.locations[key].name)
        return self.locations[key]


def group_rows(rows):
    """Строки → группы-тренировки по ключу «дата, время начала, вид спорта».

    Время в ключе затем, чтобы утренняя и вечерняя тренировки одного дня не
    склеились в одну. Группируем словарём, а не подряд идущими строками: в
    таблице, которую вели руками, строки бывают перемешаны.
    """
    groups = {}
    for row in rows:
        key = (row.date, row.start_time, collapse_spaces(row.sport).lower())
        groups.setdefault(key, []).append(row)
    return groups


def category_for(rows):
    """Категория нового вида спорта — по содержимому его строк.

    Тай-брейк в пользу кардио: «Плавание, 40 минут» без подходов — законная
    запись, а силовая без единого подхода бессмысленна, её итог пуст.
    """
    if any(row.exercise for row in rows):
        return Sport.Category.STRENGTH
    return Sport.Category.CARDIO


def measurement_for(rows):
    """В чём меряется новое упражнение — по заполненным колонкам его строк.

    «Повторы без веса» остаются «вес × повторы»: вес 0 у весового упражнения и
    значит «со своим весом», а чистые повторы от подтягиваний по строке не
    отличить. Единицу человек поменяет на странице упражнения.
    """
    has_time = any(row.duration_sec for row in rows)
    has_weight = any(row.weight_kg for row in rows)
    if has_time and has_weight:
        return Exercise.Measurement.TIME_WEIGHT
    if has_time:
        return Exercise.Measurement.TIME
    return Exercise.Measurement.WEIGHT_REPS


def import_workbook(user, stream):
    """Прочитать книгу и записать тренировки. Кидает excel.WorkbookError."""
    rows = excel.read_sheet(stream)
    report = ImportReport()
    catalog = Catalog(user, report)
    existing = _existing_keys(user)

    for (day, start_time, _), group in group_rows(rows).items():
        first = group[0]
        problem = _group_error(day, group)
        if problem:
            report.add_error(first.number, problem)
            continue
        for row in group:
            for message in row.errors:
                report.add_error(row.number, message)

        sport = catalog.sport(first.sport, category=category_for(group))
        if (day, sport.pk) in existing:
            report.skipped += 1
            continue
        try:
            with transaction.atomic():
                created = _create_workout(user, catalog, report, sport, day, start_time, group)
        except IntegrityError:
            report.add_error(first.number, "Не удалось записать тренировку, строка пропущена.")
            continue
        if created is None:
            continue
        existing.add((day, sport.pk))
        report.workouts += 1
    return report


def _existing_keys(user):
    """Ключи уже записанных тренировок: (локальная дата, вид спорта).

    Одним запросом на весь импорт. Черновики сюда не попадают: даты у них нет, и
    пропускать импорт из-за плана было бы странно.
    """
    return {
        (timezone.localtime(started_at).date(), sport_id)
        for started_at, sport_id in Workout.objects.filter(user=user)
        .finished()
        .values_list("started_at", "sport_id")
    }


def _group_error(day, group):
    """Что не так с тренировкой целиком — одно сообщение на всю группу."""
    if day is None:
        return "нет даты, тренировку не записать."
    if not collapse_spaces(group[0].sport):
        return "не указан вид спорта."
    duration = next((row.duration_min for row in group if row.duration_min), None)
    if not duration:
        return "не указана длительность — без неё тренировка считается незаконченной."
    if duration > MAX_DURATION_MIN:
        return "слишком долгая тренировка."
    if services.combine_started_at(day, group[0].start_time) > timezone.now():
        return "дата в будущем — так записывают план, а не историю."
    return None


def _create_workout(user, catalog, report, sport, day, start_time, group):
    """Одна тренировка целиком: сама запись, подходы, заметки, кардио-детали."""
    place = next((row.location for row in group if row.location), "")
    duration = next(row.duration_min for row in group if row.duration_min)
    note = next((row.workout_note for row in group if row.workout_note), "")
    # Сразу записанная: started_at и duration_min ставятся одним create, поэтому
    # состояние «идёт» не возникает даже на мгновение и unique_live_workout_per_user
    # не при делах. Тот же приём, что у записи задним числом.
    workout = Workout.objects.create(
        user=user,
        sport=sport,
        location=catalog.location(place) if place else None,
        started_at=services.combine_started_at(day, start_time),
        duration_min=duration,
        note=note[:NOTE_MAX_LENGTH],
    )
    if sport.is_strength:
        added = _create_sets(catalog, report, workout, group)
        if not added:
            # Силовая без единого подхода — пустой итог; лучше не создавать её
            # вовсе, чем оставить в истории строку, на которую нечего смотреть.
            workout.delete()
            report.add_error(group[0].number, "в силовой тренировке нет ни одного подхода.")
            return None
        report.sets += added
        return workout

    _create_cardio(report, workout, group)
    return workout


def _create_sets(catalog, report, workout, group):
    """Подходы тренировки: по строке на подход, номера заново с единицы."""
    rows = [row for row in group if row.exercise]
    for row in group:
        if not row.exercise and (row.distance_km or row.avg_heart_rate):
            report.add_warning(
                f"«{workout.sport.name}» — силовой вид спорта, поэтому дистанция и пульс "
                "из файла не записаны."
            )
            break

    by_exercise = {}
    for row in rows:
        by_exercise.setdefault(collapse_spaces(row.exercise).lower(), []).append(row)

    sets = []
    notes = []
    for lines in by_exercise.values():
        exercise = catalog.exercise(lines[0].exercise, measurement=measurement_for(lines))
        measurement = exercise.measurement
        field_name, message = REQUIRED_FIELD[measurement]
        number = 0
        for row in lines:
            values = services.set_values(measurement, row)
            if values[field_name] < 1:
                # Молча выбрасывать нельзя: при переносе тетрадки потерянная
                # строка незаметна. Порог тот же, что у «Подход выполнен».
                report.add_error(row.number, f"{exercise.name}: {message.lower()}")
                continue
            number += 1
            sets.append(
                StrengthSet(
                    workout=workout,
                    exercise=exercise,
                    set_number=number,
                    measurement=measurement,
                    # Метка времени пустая: в таблице её нет, а NULL у выполненного
                    # подхода как раз и значит «времени не знаем».
                    done=True,
                    done_at=None,
                    **values,
                )
            )
        text = next((row.exercise_note for row in lines if row.exercise_note), "")
        if text:
            if len(text) > NOTE_MAX_LENGTH:
                report.add_warning(f"Заметка к упражнению «{exercise.name}» обрезана.")
            notes.append(
                ExerciseNote(workout=workout, exercise=exercise, text=text[:NOTE_MAX_LENGTH])
            )
    StrengthSet.objects.bulk_create(sets)
    ExerciseNote.objects.bulk_create(notes)
    return len(sets)


def _create_cardio(report, workout, group):
    """Дистанция и пульс кардио-тренировки, если они в файле есть."""
    if any(row.exercise for row in group):
        report.add_warning(
            f"«{workout.sport.name}» — кардио, поэтому упражнения из файла не записаны."
        )
    distance = next((row.distance_km for row in group if row.distance_km), None)
    if distance is None:
        # Кардио без дистанции — законная запись: «плавал 40 минут, не мерил».
        return
    pulse = next((row.avg_heart_rate for row in group if row.avg_heart_rate), None)
    CardioDetails.objects.create(workout=workout, distance_km=distance, avg_heart_rate=pulse)
