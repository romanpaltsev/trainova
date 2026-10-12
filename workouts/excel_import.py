"""Загрузка истории из книги Excel: строки листа → тренировки в базе.

Разбор ячеек живёт в excel.read_sheet, здесь — только смысл: как строки
складываются в тренировки, что делать с незнакомыми справочниками и какие
записи считать уже существующими.

Битая строка не отменяет весь файл: из-за одной опечатки в трёхсотстрочной
таблице терять остальное незачем. Каждая тренировка пишется своим savepoint'ом,
а исправленный файл можно загрузить повторно — дубли отсеются.
"""

from dataclasses import dataclass, field
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from workouts import excel, services
from workouts.models import (
    NOTE_MAX_LENGTH,
    REQUIRED_FIELD,
    CardioPart,
    Exercise,
    ExerciseMachine,
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


@dataclass
class ImportReport(excel.ReportLog):
    """Что получилось: числа для итога и списки для разбора полётов.

    Ошибки и предупреждения — в общем журнале excel.ReportLog."""

    workouts: int = 0
    sets: int = 0
    skipped: int = 0
    machines: int = 0
    created_sports: list[str] = field(default_factory=list)
    created_exercises: list[str] = field(default_factory=list)
    created_locations: list[str] = field(default_factory=list)

    @property
    def created_anything(self):
        return bool(self.created_sports or self.created_exercises or self.created_locations)


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


@dataclass
class MachineOffer:
    """Тренажёр упражнения из строк одной записанной тренировки."""

    started_at: datetime
    exercise: Exercise
    location: Location
    brand: str
    model: str


class StrengthMachines:
    """Тренажёры силовых упражнений из файла — настройка «упражнение × место».

    ExerciseMachine — текущая настройка, а не часть тренировки: выгрузка пишет в
    строки тот тренажёр, что указан сейчас. Поэтому загрузка только заполняет
    пустое — уже указанный тренажёр файл не меняет, иначе старая выгрузка
    откатила бы исправленный с тех пор, — а из нескольких тренировок с одним
    упражнением в одном месте побеждает самая свежая. Пишутся после всех
    тренировок и только из записанных: строки тренировок, пропущенных как дубль
    или не прошедших проверку, не в счёт.
    """

    def __init__(self, user):
        self.user = user
        self.offers = {}

    def offer(self, workout):
        """Тренажёры только что записанной тренировки (workout.machines_in_file)."""
        for exercise, brand, model in workout.machines_in_file:
            key = (exercise.pk, workout.location_id)
            known = self.offers.get(key)
            if known is None or workout.started_at >= known.started_at:
                self.offers[key] = MachineOffer(
                    workout.started_at, exercise, workout.location, brand, model
                )

    def save(self, report):
        if not self.offers:
            return
        taken = set(
            ExerciseMachine.objects.filter(user=self.user).values_list("exercise_id", "location_id")
        )
        for key, offer in self.offers.items():
            if key in taken:
                continue
            # Названия — ссылки на справочник тренажёров тем же контрактом, что
            # в окне выбора: совпало имя — это он, незнакомое заводится своим.
            brand = services.machine_brand_for_name(self.user, offer.brand)
            model = (
                services.machine_model_for_name(self.user, brand, offer.model)
                if offer.model
                else None
            )
            # get_or_create, а не create: тренажёр, указанный в другой вкладке
            # посреди загрузки, остаётся как есть.
            _, created = ExerciseMachine.objects.get_or_create(
                user=self.user,
                exercise=offer.exercise,
                location=offer.location,
                defaults={"brand": brand, "model": model},
            )
            report.machines += created


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
    for row in rows:
        if row.broken:
            report.add_error(row.number, " ".join(row.errors) + " Строка пропущена.")
    catalog = Catalog(user, report)
    machines = StrengthMachines(user)
    existing = _existing_keys(user)

    for (day, start_time, _), group in group_rows(row for row in rows if not row.broken).items():
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
        machines.offer(created)
        existing.add((day, sport.pk))
        report.workouts += 1
    machines.save(report)
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
    if duration > excel.MAX_DURATION_MIN:
        return "слишком долгая тренировка."
    if services.combine_started_at(day, group[0].start_time) > timezone.now():
        return "дата в будущем — так записывают план, а не историю."
    return None


def _create_workout(user, catalog, report, sport, day, start_time, group):
    """Одна тренировка целиком: сама запись, подходы, заметки, кардио-части.

    Что в ней окажется, решают строки, а не категория вида спорта, — как и везде
    в приложении: силовая с пробежкой — одна тренировка, и у кардио-хозяина
    бывают подходы. Тренажёры упражнений из строк копятся в
    workout.machines_in_file: это настройка места, а не часть тренировки, и
    записывает их StrengthMachines после всех тренировок.
    """
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
    workout.machines_in_file = []
    set_rows = [row for row in group if not row.is_cardio]
    cardio_rows = [row for row in group if row.is_cardio]
    added = _create_sets(catalog, report, workout, set_rows) if set_rows else 0
    parts = _create_parts(
        catalog, report, workout, cardio_rows, lone=not set_rows and len(cardio_rows) == 1
    )
    if sport.is_strength and not added and not parts:
        # Пустая силовая — пустой итог; лучше не создавать её вовсе, чем оставить
        # в истории строку, на которую нечего смотреть.
        workout.delete()
        report.add_error(group[0].number, "в силовой тренировке нет ни подходов, ни кардио.")
        return None
    report.sets += added
    return workout


def _create_sets(catalog, report, workout, rows):
    """Подходы тренировки: по строке на подход, номера заново с единицы."""
    by_exercise = {}
    for row in rows:
        by_exercise.setdefault(collapse_spaces(row.exercise).lower(), []).append(row)

    sets = []
    notes = []
    for lines in by_exercise.values():
        exercise = catalog.exercise(lines[0].exercise, measurement=measurement_for(lines))
        measurement = exercise.measurement
        field_name, message = REQUIRED_FIELD[measurement]
        # Номер круга один на упражнение: первый заполненный из его строк.
        circuit = next((row.circuit for row in lines if row.circuit), None)
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
                    circuit=circuit,
                    **values,
                )
            )
        if number:
            # Тренажёр — только у упражнения, чьи подходы записаны: строки, ушедшие
            # в отчёт ошибками, ничего не настраивают.
            _offer_machine(report, workout, exercise, lines)
        text = next((row.exercise_note for row in lines if row.exercise_note), "")
        if text:
            if len(text) > NOTE_MAX_LENGTH:
                report.add_warning(f"Заметка к упражнению «{exercise.name}» обрезана.")
            notes.append(
                ExerciseNote(workout=workout, exercise=exercise, text=text[:NOTE_MAX_LENGTH])
            )
    StrengthSet.objects.bulk_create(sets)
    ExerciseNote.objects.bulk_create(notes)
    if any(row.circuit for row in sets):
        # Номер, оказавшийся у одного упражнения, — не круг: стираем сразу.
        services.drop_lone_circuits(workout)
    return len(sets)


def _offer_machine(report, workout, exercise, lines):
    """Тренажёр упражнения из его строк: первый заполненный, как номер суперсета."""
    brand = next((row.machine_brand for row in lines if row.machine_brand), "")
    model = next((row.machine_model for row in lines if row.machine_model), "")
    if not (brand or model):
        return
    if not brand:
        report.add_warning(f"Модель «{model}» без производителя не записана.")
    elif workout.location is None:
        # Тренажёр указывается для места (ExerciseMachine), а не для упражнения
        # вообще: один «Жим ногами» в двух залах — две разные машины.
        report.add_warning(
            f"Тренажёр «{brand}» у «{exercise.name}» не записан: он указывается для места, "
            "а у тренировки место не заполнено."
        )
    else:
        workout.machines_in_file.append((exercise, brand, model))


def _create_parts(catalog, report, workout, rows, *, lone):
    """Кардио-части: по одной на кардио-строку, в порядке строк — это и есть
    порядок ввода, которым у частей выражается «разминка, потом заминка».

    lone — строка одна, а подходов нет: такая часть и есть тренировка целиком,
    поэтому пустое «Время кардио» у неё — время тренировки (тот же инвариант
    держит CardioWorkoutForm.save). У части смешанной тренировки пустое время —
    «не указано»: общее время включает силовую, и темп по нему соврал бы.
    """
    created = 0
    for row in rows:
        if not row.has_cardio_part:
            continue
        sport = _part_sport(catalog, report, workout, row)
        if sport is None:
            continue
        model_name = row.machine_model
        if model_name and not row.machine_brand:
            report.add_warning(f"Модель «{model_name}» без производителя не записана.")
            model_name = ""
        # Тренажёр — тем же контрактом, что в форме: совпало имя — это он, иначе
        # заводится свой (services.machine_*_for_name).
        brand = (
            services.machine_brand_for_name(workout.user, row.machine_brand)
            if row.machine_brand
            else None
        )
        model = (
            services.machine_model_for_name(workout.user, brand, model_name)
            if brand is not None and model_name
            else None
        )
        duration = row.cardio_duration_min
        if duration is None and lone:
            duration = workout.duration_min
        CardioPart.objects.create(
            workout=workout,
            sport=sport,
            duration_min=duration,
            # Ноль дистанции — «не мерил», как и пустая ячейка.
            distance_km=row.distance_km or None,
            avg_heart_rate=row.avg_heart_rate,
            brand=brand,
            model=model,
            incline_pct=row.incline_pct,
            level=row.level,
            elevation_gain_m=row.elevation_gain_m,
        )
        created += 1
    return created


def _part_sport(catalog, report, workout, row):
    """Вид спорта кардио-части: из колонки «Кардио», пустая — вид спорта тренировки.

    Силовым он быть не может: пробежка, записанная «Силовой», попала бы в её
    рекорды и цвет (см. CardioPart.sport), — такая строка уходит в отчёт.
    """
    if row.cardio_sport:
        sport = catalog.sport(row.cardio_sport, category=Sport.Category.CARDIO)
        if sport.is_strength:
            report.add_warning(
                f"«{sport.name}» — силовой вид спорта, поэтому кардио с ним не записано."
            )
            return None
        return sport
    if workout.sport.is_strength:
        report.add_warning(
            f"Кардио в тренировке «{workout.sport.name}» не записано: укажите его вид "
            "спорта в колонке «Кардио»."
        )
        return None
    return workout.sport
