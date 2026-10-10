"""Логика живого режима, не привязанная к HTTP: подстановка подходов, группировка.

Отдельный модуль, чтобы правило «веса подставляются из последней тренировки
с этим упражнением» тестировалось без клиента и вьюх.
"""

from datetime import datetime, time
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max
from django.utils import timezone

from workouts.models import (
    MEASUREMENT_FIELDS,
    METRIC_FIELDS,
    Exercise,
    ExerciseNote,
    Location,
    MachineBrand,
    MachineModel,
    Sport,
    StrengthSet,
    collapse_spaces,
    decimal_display,
    is_machine,
    machine_label,
    metric_display,
    order_exercises,
    rest_display,
    ru_plural,
    suggests_machine,
    with_machine,
    with_weight_step,
)

# Время, которое ставим тренировке, записанной за прошедший день, когда его не
# указали: точное время постфактум не вспомнить, а модели нужен datetime.
DEFAULT_TIME = time(12, 0)


def combine_started_at(date, moment=None):
    """Начало тренировки из даты и необязательного времени.

    Время указали — берём его. Не указали: у сегодняшней тренировки ставим
    текущее, у прошедшей — полдень. Полдень сегодняшней не годится: запись,
    сделанная вечером, уехала бы в прошлое и встала в ленте не туда.

    Правило одно на кардио-форму, запись силовой задним числом и импорт из
    таблицы — иначе они разошлись бы в том, что значит пустое поле времени.
    """
    if moment is None:
        now = timezone.localtime()
        moment = now.time() if date == now.date() else DEFAULT_TIME
    return timezone.make_aware(datetime.combine(date, moment))


def location_for_name(user, name):
    """Место пользователя с таким названием: существующее или новое.

    Ввод названия и есть создание места — тот же контракт, что у
    ExerciseQuickForm.save_for_user: совпадение имени посреди тренировки значит
    «это оно», а не ошибку дубля. Регистр не важен (Lower-констрейнт), поэтому
    «спортлайф» находит «СпортЛайф» и второй записи не появляется. Написание
    при этом не канонизируется, а возвращается вместе с найденной записью — и
    переименование чинит его сразу по всей истории.

    Первое место сразу становится дефолтным: иначе человек отметил бы место у
    кардио, пошёл на силовую — и молчаливая подстановка молчала бы, пока он не
    заглянет в профиль.
    """
    existing = Location.objects.filter(owner=user, name__iexact=name).first()
    if existing is not None:
        return existing
    try:
        # Savepoint: гонка двух вкладок упирается в уникальный индекс, и
        # правильный ответ — взять созданную запись, а не отдать 500.
        with transaction.atomic():
            return Location.objects.create(
                owner=user,
                name=name,
                is_default=not Location.objects.filter(owner=user).exists(),
            )
    except IntegrityError:
        existing = Location.objects.filter(owner=user, name__iexact=name).first()
        if existing is not None:
            return existing
        # Другая гонка: «первое место» создали в двух вкладках с разными
        # названиями, и дефолт уже занят. Тогда место просто не дефолтное.
        with transaction.atomic():
            return Location.objects.create(owner=user, name=name, is_default=False)


def exercise_for_name(user, name, *, measurement=None, muscle_group="", equipment=""):
    """Видимое упражнение с таким названием или новое личное.

    Совпадение имени значит «это оно», а не ошибку дубля: посреди тренировки
    ввод знакомого названия добавляет его, а импорт таблицы не плодит двойников.
    Единица, группа мышц и снаряд применяются только к новой записи —
    переопределить измерение чужого (в том числе глобального) упражнения вводом
    его названия нельзя.
    """
    name = collapse_spaces(name)

    def visible_with_this_name():
        # Своё побеждает глобальное: у человека может оказаться личная запись с
        # тем же именем, что у глобальной (база это разрешает — владелец
        # разный), и выбор не должен зависеть от того, в каком порядке их
        # вернул Postgres.
        return (
            Exercise.objects.visible_to(user)
            .filter(name__iexact=name)
            .order_by(F("owner").desc(nulls_last=True))
            .first()
        )

    existing = visible_with_this_name()
    if existing is not None:
        return existing
    try:
        # Savepoint: гонка двух вкладок упрётся в уникальный индекс, и тогда
        # правильный ответ — взять только что созданную запись, а не отдать 500.
        with transaction.atomic():
            return Exercise.objects.create(
                owner=user,
                name=name,
                measurement=measurement or Exercise.Measurement.WEIGHT_REPS,
                muscle_group=muscle_group,
                equipment=equipment,
            )
    except IntegrityError:
        return visible_with_this_name()


def sport_for_name(user, name, *, category):
    """Видимый вид спорта с таким названием или новый личный.

    Тот же контракт, что у упражнений и мест. Форма «Свой вид спорта» им
    намеренно не пользуется: там ввод имени — заявка на создание, и дубль
    законно ошибка. В файле импорта имя — ссылка на запись, и совпадение
    значит «это оно»; категория существующего вида спорта при этом истина и
    содержимым файла не переписывается.
    """
    name = collapse_spaces(name)
    existing = Sport.objects.visible_to(user).filter(name__iexact=name).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return Sport.objects.create(owner=user, name=name, category=category)
    except IntegrityError:
        return Sport.objects.visible_to(user).get(name__iexact=name)


def machine_brand_for_name(user, name, *, shared=False):
    """Видимый производитель с таким названием или новый — тот же контракт, что у
    sport_for_name: совпавшее имя значит «это он», своё раньше общего.

    shared=True — общий (решает администратор): ищется и заводится среди общих.
    """
    name = collapse_spaces(name)
    scope = MachineBrand.objects.global_only() if shared else MachineBrand.objects.visible_to(user)

    def existing():
        return scope.filter(name__iexact=name).order_by(F("owner").desc(nulls_last=True)).first()

    found = existing()
    if found is not None:
        return found
    try:
        with transaction.atomic():
            return MachineBrand.objects.create(owner=None if shared else user, name=name)
    except IntegrityError:
        return existing()


def machine_model_for_name(user, brand, name, *, shared=False):
    """Модель производителя с таким названием или новая — контракт тот же.

    Общая модель бывает только у общего производителя: иначе её видели бы все,
    а производителя — один владелец. Нарушение — ValueError, текст для человека.
    """
    if shared and not brand.is_global:
        raise ValueError("Общая модель бывает только у общего производителя.")
    name = collapse_spaces(name)
    scope = MachineModel.objects.global_only() if shared else MachineModel.objects.visible_to(user)

    def existing():
        return (
            scope.filter(brand=brand, name__iexact=name)
            .order_by(F("owner").desc(nulls_last=True))
            .first()
        )

    found = existing()
    if found is not None:
        return found
    try:
        with transaction.atomic():
            return MachineModel.objects.create(
                owner=None if shared else user, brand=brand, name=name
            )
    except IntegrityError:
        return existing()


def last_sets(user, exercise):
    """Подходы упражнения из последней завершённой тренировки пользователя.

    Незавершённая текущая тренировка отфильтровывается сама: у неё duration_min IS NULL.
    Инвариант: в завершённой тренировке остаются только выполненные подходы.
    """
    last_workout_id = (
        StrengthSet.objects.filter(
            workout__user=user,
            workout__duration_min__isnull=False,
            exercise=exercise,
        )
        # Тот же тайбрейкер по id, что и в ленте истории.
        .order_by("-workout__started_at", "-workout_id")
        .values_list("workout_id", flat=True)
        .first()
    )
    if last_workout_id is None:
        return []
    return list(
        StrengthSet.objects.filter(workout_id=last_workout_id, exercise=exercise).order_by(
            "set_number"
        )
    )


def create_planned_sets(workout, exercise, *, done=False, circuit=None):
    """Подходы нового упражнения: копия прошлого раза или один пустой.

    Номера проставляются заново с единицы — в источнике могли быть пропуски.
    Единицу ставим здесь явно: bulk_create не вызывает save(), а без снимка
    подход упёрся бы в set_fields_match_measurement.

    done=True — для записанной тренировки (экран правки): в ней плановых
    подходов не бывает. Метки done_at у таких нет — когда их сделали, неизвестно.
    circuit — номер круга, в который упражнение добавляется («+ Упражнение в
    суперсет», «Повторить» с кругами).
    """
    previous = last_sets(workout.user, exercise)
    rows = [
        StrengthSet(
            workout=workout,
            exercise=exercise,
            set_number=number,
            measurement=exercise.measurement,
            done=done,
            circuit=circuit,
            **set_values(exercise.measurement, source),
        )
        for number, source in enumerate(previous, start=1)
    ] or [
        StrengthSet(
            workout=workout,
            exercise=exercise,
            set_number=1,
            measurement=exercise.measurement,
            done=done,
            circuit=circuit,
            **set_values(exercise.measurement, None),
        )
    ]
    StrengthSet.objects.bulk_create(rows)
    return rows


def set_values(measurement, source):
    """Значения подхода: применимые поля из источника, остальные — нули.

    Нули задаём явно: у weight_kg и reps нет default в модели. Источник мог быть
    записан в другой единице (упражнение переводили), поэтому берём из него
    только то, что подходит текущей единице.
    """
    values = {"weight_kg": 0, "reps": 0, "duration_sec": 0}
    if source is not None:
        values.update({field: getattr(source, field) for field in MEASUREMENT_FIELDS[measurement]})
    return values


def exercise_groups(workout):
    """Упражнения тренировки в порядке фактического выполнения, со своими подходами.

    Порядок — по самой ранней метке выполнения (`StrengthSet.done_at`); упражнения,
    которых ещё не делали, идут после всех выполненных в порядке добавления
    (он восстанавливается по id подходов — плановые строки создаются в момент
    добавления упражнения). Правило одно на все экраны: `exercise_order_key`.

    Сортировка идёт в Python по уже загруженным строкам: их в тренировке десятки,
    а оконная функция в SQL удорожила бы горячий запрос живого экрана.
    """
    # Шаг веса и тренажёр места подмешиваются той же выборкой: отдельный
    # запрос на упражнение сделал бы экран зависимым от их числа.
    rows = with_weight_step(workout.sets.select_related("exercise"), workout.user_id)
    rows = list(with_machine(rows, workout.user_id, workout.location_id).order_by("id"))
    # Заметки одним запросом на всю тренировку: в цикле по группам это был бы
    # запрос на упражнение, и бюджет экрана рос бы вместе с их числом.
    return group_sets(rows, notes_by_exercise(workout) if rows else {})


def group_sets(rows, notes=None):
    """Группы упражнений из уже загруженных подходов — чистая часть exercise_groups.

    Запросов не делает и потому годится там, где подходы уже выбраны пачкой на
    несколько тренировок: выгрузка в файл обязана расставить упражнения тем же
    порядком, что экран итога, и переизобретать правило для неё нельзя.

    `rows` ожидаются отсортированными по id — на этом держится first_set_id.
    """
    notes = notes or {}
    groups = []
    index = {}
    for row in rows:
        if row.exercise_id not in index:
            index[row.exercise_id] = len(groups)
            # first_set_id — id первого встреченного подхода: строки уже
            # отсортированы по id, значит это минимум без отдельного прохода.
            groups.append(
                {
                    "exercise": row.exercise,
                    "sets": [],
                    "first_set_id": row.pk,
                    "first_done_at": None,
                }
            )
        groups[index[row.exercise_id]]["sets"].append(row)
    for group in groups:
        # min по всем подходам, а не метка подхода с наименьшим номером: после
        # отмены и повторного выполнения метка первого подхода может стать позже.
        group["first_done_at"] = min(
            (row.done_at for row in group["sets"] if row.done_at is not None), default=None
        )
        # Номер круга у всех подходов упражнения один; min терпит расхождение
        # (как Min в SQL у двух других потребителей) — правило то же.
        group["circuit"] = min(
            (row.circuit for row in group["sets"] if row.circuit is not None), default=None
        )
    groups = order_exercises(groups)
    for position, group in enumerate(groups, start=1):
        # Номер упражнения на экране. Считается здесь, а не в шаблоне: живой экран
        # разрезает этот список на «сейчас / дальше / выполнено», и forloop.counter
        # дал бы в каждом разделе свою единицу вместо сквозной нумерации.
        group["position"] = position
        group["sets"].sort(key=lambda item: item.set_number)
        group["note"] = notes.get(group["exercise"].pk, "")
        # Тренажёр — из аннотации with_machine (одинаков у всех подходов
        # упражнения); у выборок без неё, как в выгрузке, — пусто.
        first = group["sets"][0]
        group["machine"] = machine_label(
            getattr(first, "machine_brand", ""), getattr(first, "machine_model", "")
        )
        group["suggests_machine"] = suggests_machine(group["exercise"])
        group["is_machine"] = is_machine(group["exercise"])
        # Номер подхода на экране — позиция в списке: в set_number бывают пропуски.
        for set_position, row in enumerate(group["sets"], start=1):
            row.display_number = set_position
    return groups


def notes_by_exercise(workout):
    """Заметки тренировки: {exercise_id: текст}."""
    return dict(ExerciseNote.objects.filter(workout=workout).values_list("exercise_id", "text"))


def last_note(previous, exercise):
    """Заметка из той же тренировки, что дала подходы для подстановки.

    Именно из той же: подсказка «прошлый раз» уже говорит об одной конкретной
    тренировке, и заметка рядом должна быть про неё, а не про какую-то давнюю.
    """
    if not previous:
        return ""
    return (
        ExerciseNote.objects.filter(workout_id=previous[0].workout_id, exercise=exercise)
        .values_list("text", flat=True)
        .first()
        or ""
    )


def drop_orphan_notes(workout):
    """Убрать заметки упражнений, которых в тренировке больше нет.

    Группы строятся из подходов, поэтому такая заметка уже не видна — но всплыла бы,
    если то же упражнение добавить в тренировку снова.
    """
    ExerciseNote.objects.filter(workout=workout).exclude(
        exercise__in=workout.sets.values("exercise")
    ).delete()


# ---------- Круги: суперсет, трисет, суперсет из N ----------
#
# В коде группа — circuit, «круг», а один проход по всем её упражнениям —
# раунд. В интерфейсе это «суперсет» и «подход суперсета»: одно слово «круг»
# значило там и группу («Изменить круг»), и проход («+ Круг»).


def circuit_label(size):
    """Подпись круга по числу упражнений: «Суперсет», «Трисет», «Суперсет · 5 упражнений»."""
    if size == 2:
        return "Суперсет"
    if size == 3:
        return "Трисет"
    return f"Суперсет · {size} {ru_plural(size, 'упражнение', 'упражнения', 'упражнений')}"


def round_scan(members):
    """Следующий подход круга: (группа, индекс раунда) или None, если круг пройден.

    Раунд — позиция подхода внутри упражнения, члены — в порядке круга:
    A1 → B1 → C1 → A2 → … Упражнение с меньшим числом подходов в поздних
    раундах просто пропускается.
    """
    rounds = max((len(member["sets"]) for member in members), default=0)
    for index in range(rounds):
        for member in members:
            sets = member["sets"]
            if index < len(sets) and not sets[index].done:
                return member, index
    return None


def blocks(groups):
    """Упорядоченные группы, сложенные в блоки: одно упражнение или круг.

    Члены круга уже стоят подряд (order_exercises), поэтому блок — это просто
    подряд идущие группы с одним номером круга.
    """
    result = []
    for group in groups:
        circuit = group.get("circuit")
        if circuit is not None and result and result[-1]["circuit"] == circuit:
            result[-1]["members"].append(group)
            continue
        result.append(
            {"circuit": circuit, "circuit_no": group.get("circuit_no"), "members": [group]}
        )
    for block in result:
        if block["circuit"] is None:
            continue
        members = block["members"]
        block["label"] = circuit_label(len(members))
        block["rounds"] = max(len(member["sets"]) for member in members)
        found = round_scan(members)
        # «подход 2 из 3»: раунд следующего подхода, а у пройденного круга — последний.
        block["round"] = found[1] + 1 if found else block["rounds"]
        rounds = block["rounds"]
        block["rounds_label"] = f"{rounds} {ru_plural(rounds, 'подход', 'подхода', 'подходов')}"
    return result


def valid_circuit(workout, raw):
    """Номер круга из запроса, если в тренировке есть такой круг (≥ 2 упражнений).

    Иначе None: устаревшая вкладка («+ Упражнение в суперсет» к уже разобранному
    кругу) просто добавляет упражнение отдельно, а не заводит круг из одного.
    """
    if not str(raw or "").isdecimal():
        return None
    number = int(raw)
    members = workout.sets.filter(circuit=number).values("exercise").distinct().count()
    return number if members >= 2 else None


def advance_circuit(workout, row):
    """После подхода в круге: кто следующий и закрыт ли раунд.

    Возвращает (restart_timer, stop_timer). Отдых — только когда раунд закрыт
    (или круг пройден); посреди раунда идущий с прошлого раунда отсчёт гасится,
    иначе он пискнул бы посреди следующего упражнения. Текущим становится
    следующий по обходу раундов — ручной выбор члена круга действует на один
    подход. Подход вне круга — прежнее поведение: (True, False).
    """
    groups = exercise_groups(workout)
    members = [group for group in groups if group["circuit"] == row.circuit]
    if row.circuit is None or len(members) < 2:
        return True, False
    member = next(group for group in members if group["exercise"].pk == row.exercise_id)
    index = next(position for position, item in enumerate(member["sets"]) if item.pk == row.pk)
    round_closed = all(group["sets"][index].done for group in members if index < len(group["sets"]))
    found = round_scan(members)
    workout.current_exercise_id = found[0]["exercise"].pk if found else None
    workout.save(update_fields=["current_exercise"])
    return round_closed, not round_closed


def drop_lone_circuits(workout):
    """Стереть номера кругов, в которых осталось меньше двух упражнений.

    Такой номер кругом уже не считается (order_exercises), но, оставшись в
    базе, неожиданно «склеил» бы упражнение с будущим кругом того же номера.
    """
    lone = list(
        workout.sets.exclude(circuit=None)
        .values("circuit")
        .annotate(members=Count("exercise", distinct=True))
        .filter(members__lt=2)
        .values_list("circuit", flat=True)
    )
    if lone:
        workout.sets.filter(circuit__in=lone).update(circuit=None)


def link_with_next(workout, exercise_id, next_id):
    """Связать упражнение со следующим в круг. False — пара уже не соседняя.

    Оба id приходят из окна «Суперсеты»: если вкладка устарела — между ними что-то
    появилось или они уже в одном круге, — ничего не меняем, окно перерисуется
    с настоящим порядком. Иначе: новый круг, присоединение к кругу соседа или
    слияние двух кругов в первый.
    """
    groups = exercise_groups(workout)
    order = [group["exercise"].pk for group in groups]
    if exercise_id not in order:
        return False
    index = order.index(exercise_id)
    if index + 1 >= len(order) or order[index + 1] != next_id:
        return False
    first, second = groups[index], groups[index + 1]
    if first["circuit"] is not None and first["circuit"] == second["circuit"]:
        return False
    if first["circuit"] is not None and second["circuit"] is not None:
        workout.sets.filter(circuit=second["circuit"]).update(circuit=first["circuit"])
    elif first["circuit"] is not None:
        workout.sets.filter(exercise_id=next_id).update(circuit=first["circuit"])
    elif second["circuit"] is not None:
        workout.sets.filter(exercise_id=exercise_id).update(circuit=second["circuit"])
    else:
        # Новый номер — после всех, что есть, включая одинокие остатки: так
        # остаток не склеится с новым кругом.
        number = (workout.sets.aggregate(top=Max("circuit"))["top"] or 0) + 1
        workout.sets.filter(exercise_id__in=[exercise_id, next_id]).update(circuit=number)
    return True


def unlink(workout, exercise_id):
    """Убрать упражнение из круга; круг из одного упражнения разбирается."""
    workout.sets.filter(exercise_id=exercise_id).update(circuit=None)
    drop_lone_circuits(workout)


def add_round(workout, circuit):
    """«+ Подход суперсета»: по подходу каждому упражнению круга — копия его последнего.

    На записанной тренировке подходы сразу выполненные, без метки времени, —
    как у «+ подход» на экране правки. False — такого круга уже нет.
    """
    members = [group for group in exercise_groups(workout) if group["circuit"] == circuit]
    if len(members) < 2:
        return False
    with transaction.atomic():
        for member in members:
            exercise = member["exercise"]
            last = member["sets"][-1]
            StrengthSet.objects.create(
                workout=workout,
                exercise=exercise,
                set_number=max(row.set_number for row in member["sets"]) + 1,
                measurement=exercise.measurement,
                done=workout.is_finished,
                circuit=circuit,
                **set_values(exercise.measurement, last),
            )
    return True


def live_groups(workout, open_set_id=None):
    """Группы для живого экрана: у каждой статус current / queue / done и подсказка.

    open_set_id — плановый подход текущего упражнения, открытый тапом по его
    строке (приём экрана правки). Чужой, выполненный или устаревший id просто
    не найдётся среди невыполненных, и откроется текущий подход.
    """
    groups = exercise_groups(workout)
    pending_ids = [g["exercise"].pk for g in groups if any(not s.done for s in g["sets"])]
    current_id = None
    if pending_ids:
        # Выбранное вручную упражнение — текущее, пока у него есть невыполненные подходы.
        if workout.current_exercise_id in pending_ids:
            current_id = workout.current_exercise_id
        else:
            # Первое по общему порядку, то есть «раньше начатое незакрытое, а
            # если начатых нет — первое по плану»: возвращает к тому, что не
            # доделал, а не к первому добавленному. У круга — тот, чья очередь
            # в раунде (A1 → B1 → A2 …), а не первый незакрытый.
            first = next(g for g in groups if g["exercise"].pk == pending_ids[0])
            current_id = pending_ids[0]
            if first["circuit"] is not None:
                members = [g for g in groups if g["circuit"] == first["circuit"]]
                found = round_scan(members)
                if found is not None:
                    current_id = found[0]["exercise"].pk

    for group in groups:
        sets = group["sets"]
        group["done_sets"] = [s for s in sets if s.done]
        if group["exercise"].pk == current_id:
            group["state"] = "current"
            pending = [s for s in sets if not s.done]
            group["current_set"] = pending[0]
            # Панель степперов одна на карточку — у открытого подхода, по
            # умолчанию у текущего: две панели на 375px растянули бы карточку на
            # весь экран. Остальные плановые — строки до и после неё.
            index = next((i for i, s in enumerate(pending) if s.pk == open_set_id), 0)
            group["open_set"] = pending[index]
            group["before_open"] = pending[:index]
            group["after_open"] = pending[index + 1 :]
            previous = last_sets(workout.user, group["exercise"])
            group["hint"] = last_time_hint(previous)
            # Заметка только у текущего упражнения: у строк очереди подсказка
            # короткая, а запрос на каждую сделал бы экран зависимым от их числа.
            group["last_note"] = last_note(previous, group["exercise"])
        elif group["exercise"].pk in pending_ids:
            group["state"] = "queue"
            group["hint"] = queue_hint(sets)
        else:
            group["state"] = "done"
            group["hint"] = done_hint(sets)
    return groups


def live_context(workout, open_set_id=None):
    """Контекст региона упражнений: группы и блоки, разложенные по статусам.

    Блок — одно упражнение или круг. Круг стоит в разделе своего самого
    «живого» члена: с текущим — в «Сейчас», с невыполненными — в «Дальше»,
    пройденный целиком — в «Выполнено»; внутри члены рисуются по своему статусу.
    """
    groups = live_groups(workout, open_set_id)
    all_blocks = blocks(groups)
    for block in all_blocks:
        states = {member["state"] for member in block["members"]}
        block["state"] = next(state for state in ("current", "queue", "done") if state in states)
    return {
        "workout": workout,
        "current_group": next((g for g in groups if g["state"] == "current"), None),
        "queue_groups": [g for g in groups if g["state"] == "queue"],
        "done_groups": [g for g in groups if g["state"] == "done"],
        "current_block": next((b for b in all_blocks if b["state"] == "current"), None),
        "queue_blocks": [b for b in all_blocks if b["state"] == "queue"],
        "done_blocks": [b for b in all_blocks if b["state"] == "done"],
        "exercises_count": len(groups),
    }


def set_value_hint(row):
    """Короткая запись подхода для перечисления: «77,5×8» · «8» · «1:30»."""
    measurement = row.measurement
    if measurement == Exercise.Measurement.WEIGHT_REPS:
        return f"{row.weight_display}×{row.reps}"
    if measurement == Exercise.Measurement.TIME_WEIGHT:
        return f"{rest_display(row.duration_sec)}×{row.weight_display}"
    if measurement == Exercise.Measurement.REPS:
        return str(row.reps)
    return rest_display(row.duration_sec)


def last_time_hint(previous):
    """«прошлый раз: 70×10 · 77,5×8» — или «1:00 · 1:15» у удержаний."""
    if not previous:
        return "первое выполнение"
    return "прошлый раз: " + " · ".join(set_value_hint(row) for row in previous)


def queue_hint(sets):
    """Подсказка строки очереди: «прошлый раз: 3 подхода · до 100 кг»."""
    done_count = sum(1 for s in sets if s.done)
    if done_count:
        return f"выполнено {done_count} из {len(sets)}"
    # Максимум метрики, а не веса: у планки и подтягиваний вес нулевой, и по нему
    # подсказка всегда говорила бы «первое выполнение» даже с полной историей.
    top = max(row.metric_value for row in sets)
    if not top:
        return "первое выполнение"
    count = len(sets)
    sets_word = ru_plural(count, "подход", "подхода", "подходов")
    return f"прошлый раз: {count} {sets_word} · до {metric_display(sets[0].measurement, top)}"


def done_hint(sets):
    """Итог завершённого упражнения: «3 подхода · 590 кг» или «· 4:30»."""
    count = len(sets)
    sets_word = ru_plural(count, "подход", "подхода", "подходов")
    return f"{count} {sets_word} · {exercise_total(sets)}"


def exercise_total(sets):
    """Сумма работы упражнения в его единице: тоннаж, повторы или время."""
    measurement = sets[0].measurement
    if METRIC_FIELDS[measurement] == "duration_sec":
        return rest_display(sum(row.duration_sec for row in sets))
    if measurement == Exercise.Measurement.REPS:
        total = sum(row.reps for row in sets)
        return f"{total} {ru_plural(total, 'повтор', 'повтора', 'повторов')}"
    tonnage = sum((row.tonnage_kg for row in sets), Decimal(0))
    return f"{decimal_display(tonnage)} кг"
