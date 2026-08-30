from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.timezone import localtime

# Выше этой скорости показываем км/ч (вело, лыжи), ниже — мин/км (бег, ходьба).
# Порог, а не поле в Sport: набор полей справочника зафиксирован в CLAUDE.md,
# а личных видов спорта может быть сколько угодно. Дубль порога есть в static/js/cardio.js.
SPEED_THRESHOLD_KMH = Decimal("14")

# Цветовой токен закреплён за видом спорта, а не за порядком в наборе.
GLOBAL_SPORT_COLORS = {
    "силовая": "strength",
    "велосипед": "bike",
    "бег": "run",
    "лыжи": "ski",
}


# Границы и шаг отдыха: одни и те же для значения по умолчанию в профиле
# (User.rest_seconds_default) и для отдыха отдельной тренировки (Workout.rest_seconds).
REST_MIN_SECONDS = 15
REST_MAX_SECONDS = 600
REST_DELTAS = {"-15", "15"}


def clamp_rest_seconds(seconds):
    return max(REST_MIN_SECONDS, min(REST_MAX_SECONDS, seconds))


def rest_display(seconds):
    """Секунды как 1:30 — так показаны и отдых, и удержание подхода."""
    return f"{seconds // 60}:{seconds % 60:02d}"


def decimal_display(value):
    """Число без лишних нулей и с запятой: 7,2 · 32,4 · 12,34 · 10."""
    value = value.normalize()
    if value == value.to_integral():
        value = value.quantize(Decimal(1))
    return f"{value}".replace(".", ",")


def ru_plural(number, one, few, many):
    """Русское склонение: 1 подход, 2 подхода, 5 подходов.

    Живёт рядом с остальными форматтерами: подписи значений собираются здесь же,
    а не в services, чтобы модель умела показать своё значение сама.
    """
    tail = abs(number) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


class CatalogQuerySet(models.QuerySet):
    """Общее поведение гибридных справочников (Sport, Exercise)."""

    def visible_to(self, user):
        """Глобальные записи (owner IS NULL) плюс собственные записи пользователя."""
        return self.filter(Q(owner__isnull=True) | Q(owner=user))

    def global_only(self):
        return self.filter(owner__isnull=True)


CatalogManager = models.Manager.from_queryset(CatalogQuerySet)


class CatalogItem(models.Model):
    """Запись справочника: owner IS NULL — глобальная, правит только админ."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="владелец",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        # %(class)s → user.sports и user.exercises
        related_name="%(class)ss",
        help_text="Пусто — глобальная запись, видна всем пользователям.",
    )

    objects = CatalogManager()

    class Meta:
        abstract = True

    def __str__(self):
        return self.name

    @property
    def is_global(self):
        return self.owner_id is None


class Sport(CatalogItem):
    class Category(models.TextChoices):
        STRENGTH = "strength", "Силовая"
        CARDIO = "cardio", "Кардио"

    name = models.CharField("название", max_length=60)
    category = models.CharField("категория", max_length=10, choices=Category)

    class Meta(CatalogItem.Meta):
        abstract = False
        verbose_name = "вид спорта"
        verbose_name_plural = "виды спорта"
        ordering = ["name"]
        constraints = [
            # В Postgres NULL-ы не равны друг другу, поэтому одного ограничения
            # с owner мало: глобальные записи оно не защищает.
            models.UniqueConstraint(
                Lower("name"),
                "owner",
                name="unique_sport_name_per_owner",
                violation_error_message="Вид спорта с таким названием у вас уже есть.",
            ),
            models.UniqueConstraint(
                Lower("name"),
                condition=Q(owner__isnull=True),
                name="unique_global_sport_name",
                violation_error_message="Глобальный вид спорта с таким названием уже есть.",
            ),
        ]

    @property
    def is_strength(self):
        return self.category == self.Category.STRENGTH

    @property
    def color_key(self):
        """Ключ цветового токена --app-sport-*; для личных видов — по категории."""
        known = GLOBAL_SPORT_COLORS.get(self.name.strip().lower())
        if known:
            return known
        return "strength" if self.is_strength else "run"


class Exercise(CatalogItem):
    class Measurement(models.TextChoices):
        """В чём считается подход. Планку не взвешивают, подтягивания не всегда."""

        WEIGHT_REPS = "weight_reps", "Вес × повторы"
        REPS = "reps", "Повторы"
        TIME = "time", "Время"
        TIME_WEIGHT = "time_weight", "Время + вес"

    name = models.CharField("название", max_length=80)
    muscle_group = models.CharField("группа мышц", max_length=60, blank=True)
    measurement = models.CharField(
        "измерение",
        max_length=12,
        choices=Measurement,
        default=Measurement.WEIGHT_REPS,
        help_text="Килограммы с повторами, только повторы или время удержания.",
    )

    class Meta(CatalogItem.Meta):
        abstract = False
        verbose_name = "упражнение"
        verbose_name_plural = "упражнения"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "owner",
                name="unique_exercise_name_per_owner",
                violation_error_message="Упражнение с таким названием у вас уже есть.",
            ),
            models.UniqueConstraint(
                Lower("name"),
                condition=Q(owner__isnull=True),
                name="unique_global_exercise_name",
                violation_error_message="Глобальное упражнение с таким названием уже есть.",
            ),
        ]


# Какие поля подхода осмысленны для каждой единицы — и в каком порядке они стоят
# на экране. Остальные поля обязаны быть нулём: это же проверяет ограничение
# set_fields_match_measurement. Больше двух полей не бывает: на 375px три
# степпера (две кнопки 44px плюс значение) не помещаются.
MEASUREMENT_FIELDS = {
    Exercise.Measurement.WEIGHT_REPS: ("weight_kg", "reps"),
    Exercise.Measurement.REPS: ("reps",),
    Exercise.Measurement.TIME: ("duration_sec",),
    Exercise.Measurement.TIME_WEIGHT: ("duration_sec", "weight_kg"),
}

# Метрика упражнения — поле подхода, по которому считаются рекорд и прогресс,
# и подпись к нему. Тот же приём, что у кардио: единица выбирается по данным,
# а не зашита в шаблон (ср. CardioDetails.metric_label).
METRIC_FIELDS = {
    Exercise.Measurement.WEIGHT_REPS: "weight_kg",
    Exercise.Measurement.REPS: "reps",
    Exercise.Measurement.TIME: "duration_sec",
    Exercise.Measurement.TIME_WEIGHT: "duration_sec",
}
METRIC_LABELS = {
    Exercise.Measurement.WEIGHT_REPS: "вес",
    Exercise.Measurement.REPS: "повторы",
    Exercise.Measurement.TIME: "удержание",
    Exercise.Measurement.TIME_WEIGHT: "удержание",
}
# Единица оси графика и тултипа; у времени формат особый — «1:30», а не «90 сек».
METRIC_UNITS = {
    Exercise.Measurement.WEIGHT_REPS: "кг",
    Exercise.Measurement.REPS: "",
    Exercise.Measurement.TIME: "",
    Exercise.Measurement.TIME_WEIGHT: "",
}
# Единицы, у которых метрика — секунды: их значения показываются как «1:30».
TIME_MEASUREMENTS = frozenset({Exercise.Measurement.TIME, Exercise.Measurement.TIME_WEIGHT})

# Шаг степпера по полю подхода. Живёт здесь, а не во вьюхе: по нему считается
# арифметика тапа и подписывается кнопка, и расходиться этим двум нельзя.
SET_STEPS = {"weight_kg": Decimal("2.5"), "reps": 1, "duration_sec": 15}
# Подпись поля и то, что читает скринридер на кнопках «−» и «+».
FIELD_INPUT = {
    "weight_kg": {"label": "Вес, кг", "aria": "2,5 кг"},
    "reps": {"label": "Повторы", "aria": "повтор"},
    "duration_sec": {"label": "Время", "aria": "15 секунд"},
}


def step_display(field):
    """Подпись шага под степпером: «2,5» · «1» · «15 с»."""
    step = SET_STEPS[field]
    if field == "duration_sec":
        return f"{step} с"
    return decimal_display(step) if isinstance(step, Decimal) else str(step)


def metric_display(measurement, value):
    """Значение метрики с единицей: «83,75 кг», «12 повторов», «1:30»."""
    if measurement in TIME_MEASUREMENTS:
        return rest_display(int(value))
    if measurement == Exercise.Measurement.REPS:
        count = int(value)
        return f"{count} {ru_plural(count, 'повтор', 'повтора', 'повторов')}"
    return f"{decimal_display(Decimal(str(value)))} кг"


class WorkoutQuerySet(models.QuerySet):
    """Три состояния тренировки собираются из двух колонок — см. Workout."""

    def finished(self):
        """Завершённые тренировки — только они попадают в историю и агрегаты."""
        return self.filter(duration_min__isnull=False)

    def live(self):
        """Идущая: время пошло, длительности ещё нет. Больше одной быть не может."""
        return self.filter(started_at__isnull=False, duration_min__isnull=True)

    def planned(self):
        """Черновики: подготовлены заранее, время не идёт. Их может быть сколько угодно.

        Одного условия хватает: check-констрейнт запрещает завершённую без начала,
        поэтому started_at IS NULL уже означает и duration_min IS NULL.
        """
        return self.filter(started_at__isnull=True)

    def unfinished(self):
        """Черновик или идущая — всё, что ещё правится живым режимом."""
        return self.filter(duration_min__isnull=True)


class Workout(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="пользователь",
        on_delete=models.CASCADE,
        related_name="workouts",
    )
    sport = models.ForeignKey(
        Sport,
        verbose_name="вид спорта",
        on_delete=models.PROTECT,
        related_name="workouts",
    )
    # NULL = тренировка подготовлена заранее, но не начата (черновик): времени
    # начала у неё честно нет, поэтому и отсчёт длительности невозможен.
    started_at = models.DateTimeField(
        "начало",
        null=True,
        blank=True,
        help_text="Пусто — тренировка подготовлена, но ещё не начата.",
    )
    # NULL = тренировка ещё идёт (живой режим); длительность считается при завершении.
    duration_min = models.PositiveIntegerField(
        "длительность, мин",
        null=True,
        blank=True,
        help_text="Пусто — тренировка ещё идёт.",
    )
    note = models.TextField("заметка", blank=True)
    rest_seconds = models.PositiveSmallIntegerField(
        "отдых между подходами, сек",
        null=True,
        blank=True,
        help_text="Пусто — берётся значение по умолчанию из профиля.",
    )
    current_exercise = models.ForeignKey(
        Exercise,
        verbose_name="текущее упражнение",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Выбранное вручную упражнение живого режима.",
    )

    objects = models.Manager.from_queryset(WorkoutQuerySet)()

    class Meta:
        verbose_name = "тренировка"
        verbose_name_plural = "тренировки"
        # -id как тайбрейкер: у двух тренировок одного прошедшего дня started_at
        # совпадает (полдень), и без него порядок между запросами не определён,
        # а пагинация может продублировать или потерять карточку.
        # nulls_last: в Postgres DESC ставит NULL первыми, и черновики всплывали бы
        # в начало любой выборки без явной сортировки — там, где ищут идущую.
        ordering = [F("started_at").desc(nulls_last=True), "-id"]
        indexes = [models.Index(fields=["user", "-started_at"], name="workout_user_started_idx")]
        constraints = [
            # Гонку «две вкладки нажали старт» ловит база, а не check-then-create.
            # Предикат требует начала: черновиков может быть сколько угодно, они
            # в индекс не попадают, а вот UPDATE «Начать» второго — попадает.
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(started_at__isnull=False, duration_min__isnull=True),
                name="unique_live_workout_per_user",
                violation_error_message="У вас уже есть незавершённая тренировка.",
            ),
            # Записанной без начала не бывает. Благодаря этому .finished() гарантирует
            # непустой started_at, и localtime(started_at) в истории и агрегатах
            # не может молча подставить now() вместо настоящей даты.
            models.CheckConstraint(
                condition=Q(started_at__isnull=False) | Q(duration_min__isnull=True),
                name="finished_workout_is_started",
                violation_error_message="Завершённая тренировка не может быть без начала.",
            ),
        ]

    def __str__(self):
        if self.started_at is None:
            return f"{self.sport} — черновик"
        # localtime: started_at хранится в UTC, а показывать надо в TIME_ZONE проекта.
        return f"{self.sport} — {localtime(self.started_at):%d.%m.%Y %H:%M}"

    @property
    def is_finished(self):
        return self.duration_min is not None

    @property
    def is_planned(self):
        """Черновик: подготовлена, но не начата — время ещё не идёт."""
        return self.started_at is None

    @property
    def duration_display(self):
        """Длительность как 1:24 — так она показана в макетах."""
        if self.duration_min is None:
            return "—"
        hours, minutes = divmod(self.duration_min, 60)
        return f"{hours}:{minutes:02d}"

    @property
    def elapsed_min(self):
        """Минуты с начала — тикающая длительность идущей тренировки."""
        if self.started_at is None:
            return 0
        return int((timezone.now() - self.started_at).total_seconds() // 60)

    @property
    def workload(self):
        """Третья метрика силовой карточки: тоннаж, повторы или удержание.

        Что показать, решает содержимое: у тренировки из одной планки тоннаж
        нулевой, и колонка «0 кг» выглядела бы сломанной. Значения ждём
        аннотациями queryset'а (`stats.WORKLOAD_ANNOTATIONS`) — считать их
        свойством означало бы отдельный запрос на каждую карточку ленты.
        """
        tonnage = getattr(self, "tonnage", None) or 0
        if tonnage:
            return {"label": "тоннаж", "value": f"{decimal_display(Decimal(str(tonnage)))} кг"}
        reps = getattr(self, "total_reps", None) or 0
        if reps:
            word = ru_plural(reps, "повтор", "повтора", "повторов")
            return {"label": "повторы", "value": f"{reps} {word}"}
        seconds = getattr(self, "total_duration", None) or 0
        if seconds:
            return {"label": "удержание", "value": rest_display(seconds)}
        return {"label": "тоннаж", "value": "—"}

    @property
    def effective_rest_seconds(self):
        """Отдых тренировки, а при пустом — значение по умолчанию из профиля.

        Именно `is not None`: 0 — валидное «без отдыха».
        """
        if self.rest_seconds is not None:
            return self.rest_seconds
        return self.user.rest_seconds_default


class StrengthSet(models.Model):
    workout = models.ForeignKey(
        Workout,
        verbose_name="тренировка",
        on_delete=models.CASCADE,
        related_name="sets",
    )
    exercise = models.ForeignKey(
        Exercise,
        verbose_name="упражнение",
        on_delete=models.PROTECT,
        related_name="sets",
    )
    set_number = models.PositiveSmallIntegerField("номер подхода")
    weight_kg = models.DecimalField(
        "вес, кг",
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    reps = models.PositiveSmallIntegerField("повторения")
    duration_sec = models.PositiveSmallIntegerField("время, сек", default=0)
    # Снимок единицы упражнения на момент записи: если упражнение потом переведут
    # в другие единицы, старая история должна читаться так, как её записали.
    measurement = models.CharField(
        "измерение",
        max_length=12,
        choices=Exercise.Measurement,
        default=Exercise.Measurement.WEIGHT_REPS,
    )
    # False = плановый подход живого режима; при завершении тренировки такие удаляются.
    done = models.BooleanField("выполнен", default=False)

    class Meta:
        verbose_name = "подход"
        verbose_name_plural = "подходы"
        ordering = ["exercise__name", "set_number"]
        # Рекорды и прогресс упражнения группируют подходы по упражнению,
        # а одиночного FK-индекса для такой выборки мало.
        indexes = [models.Index(fields=["exercise", "workout"], name="set_exercise_workout_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["workout", "exercise", "set_number"],
                name="unique_set_number_per_exercise",
                violation_error_message="Подход с таким номером для этого упражнения уже есть.",
            ),
            # Неприменимые к единице поля обязаны быть нулём: планка с повторами
            # или подтягивания с весом — это не «странные данные», а сломанный
            # подход, который потом соврёт в рекордах и тоннаже.
            # Заодно поэтому тоннаж Sum(weight × reps) остаётся верным без фильтра.
            models.CheckConstraint(
                condition=(
                    Q(measurement=Exercise.Measurement.WEIGHT_REPS, duration_sec=0)
                    | Q(measurement=Exercise.Measurement.REPS, duration_sec=0, weight_kg=0)
                    | Q(measurement=Exercise.Measurement.TIME, reps=0, weight_kg=0)
                    | Q(measurement=Exercise.Measurement.TIME_WEIGHT, reps=0)
                ),
                name="set_fields_match_measurement",
                violation_error_message="Значения подхода не совпадают с единицей упражнения.",
            ),
        ]

    def __str__(self):
        return f"{self.exercise} · {self.set_number}: {self.value_display}"

    def clean(self):
        if self.workout_id and not self.workout.sport.is_strength:
            raise ValidationError("Подходы бывают только у силовой тренировки.")

    @property
    def tonnage_kg(self):
        """Тоннаж подхода — вес, поднятый за все повторения.

        У временных подходов повторов нет, поэтому тоннаж честно нулевой.
        """
        return self.weight_kg * self.reps

    @property
    def weight_display(self):
        """Вес как в макетах: 70 · 77,5 · 82,25. Форматирует всегда сервер, не JS."""
        # str: до перечитывания из БД значение может быть числом, а не Decimal.
        return decimal_display(Decimal(str(self.weight_kg)))

    @property
    def metric_value(self):
        """Значение метрики подхода: вес, повторы или секунды удержания."""
        return getattr(self, METRIC_FIELDS[self.measurement])

    @property
    def stepper_fields(self):
        """Поля ввода этого подхода — ровно те, что осмысленны для его единицы."""
        return [
            {
                "name": name,
                "label": FIELD_INPUT[name]["label"],
                "aria": FIELD_INPUT[name]["aria"],
                "step": step_display(name),
                "value": self.field_display(name),
            }
            for name in MEASUREMENT_FIELDS[self.measurement]
        ]

    def field_display(self, field):
        """Значение одного поля для степпера: «77,5» · «8» · «1:30»."""
        if field == "duration_sec":
            return rest_display(self.duration_sec)
        if field == "weight_kg":
            return self.weight_display
        return str(self.reps)

    @property
    def value_display(self):
        """Подход строкой — единственное место, где собирается «сколько сделано».

        «83,75 кг × 8» · «8 повторов» · «1:30» · «1:30 · 20 кг».
        """
        measurement = self.measurement
        if measurement == Exercise.Measurement.TIME:
            return rest_display(self.duration_sec)
        if measurement == Exercise.Measurement.TIME_WEIGHT:
            return f"{rest_display(self.duration_sec)} · {self.weight_display} кг"
        if measurement == Exercise.Measurement.REPS:
            return f"{self.reps} {ru_plural(self.reps, 'повтор', 'повтора', 'повторов')}"
        return f"{self.weight_display} кг × {self.reps}"


class CardioDetails(models.Model):
    workout = models.OneToOneField(
        Workout,
        verbose_name="тренировка",
        on_delete=models.CASCADE,
        related_name="cardio",
    )
    distance_km = models.DecimalField(
        "дистанция, км",
        max_digits=6,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    avg_heart_rate = models.PositiveSmallIntegerField("средний пульс", null=True, blank=True)

    class Meta:
        verbose_name = "детали кардио"
        verbose_name_plural = "детали кардио"

    def __str__(self):
        return f"{self.distance_km} км"

    def clean(self):
        if self.workout_id and self.workout.sport.is_strength:
            raise ValidationError("Детали кардио бывают только у кардио-тренировки.")

    @property
    def distance(self):
        """Дистанция как Decimal: до перечитывания из БД значение может быть строкой."""
        return Decimal(str(self.distance_km)) if self.distance_km else None

    @property
    def distance_display(self):
        """Дистанция без лишних нулей и с запятой: 7,2 · 32,4 · 12,34 · 10."""
        value = self.distance
        if value is None:
            return "—"
        return decimal_display(value)

    @property
    def speed_kmh(self):
        """Средняя скорость, км/ч."""
        minutes = self.workout.duration_min
        if not minutes or not self.distance:
            return None
        return (self.distance * 60 / Decimal(minutes)).quantize(Decimal("0.1"))

    @property
    def pace_seconds_per_km(self):
        """Темп в секундах на километр."""
        # duration_min теперь nullable: чужими руками (админка) кардио без
        # длительности возможно, и деление не должно ронять страницу.
        if not self.workout.duration_min or not self.distance:
            return None
        return int(self.workout.duration_min * 60 / self.distance)

    @property
    def pace_display(self):
        """Темп как 5:41."""
        seconds = self.pace_seconds_per_km
        if seconds is None:
            return None
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes}:{seconds:02d}"

    @property
    def shows_speed(self):
        """Быстрые виды спорта удобнее читать в км/ч, медленные — в мин/км."""
        speed = self.speed_kmh
        return speed is not None and speed >= SPEED_THRESHOLD_KMH

    @property
    def metric_label(self):
        return "скорость" if self.shows_speed else "темп"

    @property
    def metric_display(self):
        if self.shows_speed:
            return f"{self.speed_kmh} км/ч".replace(".", ",")
        pace = self.pace_display
        return f"{pace} /км" if pace else "—"


class ChangelogQuerySet(models.QuerySet):
    """Новости проекта: что показывать и что считать непрочитанным."""

    def published(self, now=None):
        # Две независимые заслонки: is_published — черновик, published_at — отложенный показ.
        return self.filter(is_published=True, published_at__lte=now or timezone.now())

    def unread_for(self, user):
        """Опубликованные записи новее последнего открытия «Что нового»."""
        entries = self.published()
        if user.changelog_seen_at is None:
            # Страницу ещё не открывали — непрочитано всё.
            return entries
        return entries.filter(published_at__gt=user.changelog_seen_at)


class ChangelogEntry(models.Model):
    """Новость проекта. Создаёт и правит только админ через Django admin."""

    class Kind(models.TextChoices):
        FEATURE = "feature", "Новое"
        FIX = "fix", "Исправлено"

    kind = models.CharField("тип", max_length=10, choices=Kind, default=Kind.FEATURE)
    title = models.CharField("заголовок", max_length=120)
    body = models.TextField("текст")
    published_at = models.DateTimeField(
        "дата публикации",
        default=timezone.now,
        help_text=(
            "Можно поставить будущую — запись появится сама. Дата задним числом "
            "ниже последнего открытия «Что нового» точку-бейдж не зажжёт."
        ),
    )
    is_published = models.BooleanField(
        "опубликовано", default=True, help_text="Снято — черновик, в приложении не виден."
    )

    objects = models.Manager.from_queryset(ChangelogQuerySet)()

    class Meta:
        verbose_name = "новость"
        verbose_name_plural = "новости"
        # -id как тайбрейкер: у двух записей одного дня published_at совпадает.
        ordering = ["-published_at", "-id"]
        # Индекса нет намеренно: таблица растёт на единицы строк за релиз,
        # последовательное чтение дешевле обхода индекса.

    def __str__(self):
        return self.title
