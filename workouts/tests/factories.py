from datetime import timedelta
from decimal import Decimal

import factory
from django.utils import timezone

from accounts.tests.factories import UserFactory
from workouts.models import (
    BodyMeasurement,
    BodyMetric,
    CardioPart,
    ChangelogEntry,
    Exercise,
    ExerciseMachine,
    ExerciseNote,
    ExerciseSettings,
    Location,
    Sport,
    StrengthSet,
    Workout,
)


class SportFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Sport

    name = factory.Sequence(lambda n: f"Вид спорта {n}")
    category = Sport.Category.STRENGTH
    owner = None


class ExerciseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Exercise

    name = factory.Sequence(lambda n: f"Упражнение {n}")
    muscle_group = "Грудь"
    owner = None


class ExerciseSettingsFactory(factory.django.DjangoModelFactory):
    """Настройки упражнения у пользователя: пока это только шаг веса."""

    class Meta:
        model = ExerciseSettings

    user = factory.SubFactory(UserFactory)
    exercise = factory.SubFactory(ExerciseFactory)
    weight_step = Decimal("2.5")


class LocationFactory(factory.django.DjangoModelFactory):
    """Место тренировки. owner обязателен: глобальных мест не бывает.

    is_default по умолчанию False: частичный уникальный индекс не разрешил бы
    двум местам одного пользователя быть дефолтными, и фабрика с is_default=True
    падала бы на втором вызове.
    """

    class Meta:
        model = Location

    owner = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f"Место {n}")


class ExerciseMachineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExerciseMachine

    user = factory.SubFactory(UserFactory)
    exercise = factory.SubFactory(ExerciseFactory)
    location = factory.SubFactory(LocationFactory, owner=factory.SelfAttribute("..user"))
    brand = "Technogym"
    model = "Selection 900"


class WorkoutFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Workout

    user = factory.SubFactory(UserFactory)
    sport = factory.SubFactory(SportFactory)
    started_at = factory.LazyFunction(timezone.now)
    duration_min = 60


class StrengthSetFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StrengthSet

    workout = factory.SubFactory(WorkoutFactory)
    exercise = factory.SubFactory(ExerciseFactory)
    set_number = factory.Sequence(lambda n: n + 1)
    weight_kg = 80
    reps = 8
    # Фабрика создаёт исторический (уже выполненный) подход; плановые строки живого
    # режима в тестах создаются через эндпоинты или явным done=False.
    done = True


class TimeSetFactory(StrengthSetFactory):
    """Подход на удержание: ни веса, ни повторов — так требует ограничение БД."""

    measurement = Exercise.Measurement.TIME
    weight_kg = 0
    reps = 0
    duration_sec = 60


class RepsSetFactory(StrengthSetFactory):
    """Подход «только повторы»: подтягивания, скручивания — без веса."""

    measurement = Exercise.Measurement.REPS
    weight_kg = 0
    reps = 12


class ExerciseNoteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExerciseNote

    workout = factory.SubFactory(WorkoutFactory)
    exercise = factory.SubFactory(ExerciseFactory)
    text = "Болело плечо"


class CardioPartFactory(factory.django.DjangoModelFactory):
    """Кардио-часть. По умолчанию — единственная часть чистого кардио.

    Вид спорта и длительность берутся у тренировки: так выглядит вся история
    до появления частей, и так же их проставила миграция. У смешанной
    тренировки то и другое задаётся явно.
    """

    class Meta:
        model = CardioPart

    workout = factory.SubFactory(WorkoutFactory, sport__category=Sport.Category.CARDIO)
    sport = factory.SelfAttribute("workout.sport")
    duration_min = factory.SelfAttribute("workout.duration_min")
    distance_km = 10
    avg_heart_rate = 140


class ChangelogEntryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ChangelogEntry

    kind = ChangelogEntry.Kind.FEATURE
    title = factory.Sequence(lambda n: f"Новость {n}")
    body = "Текст новости."
    published_at = factory.LazyFunction(timezone.now)
    is_published = True


class BodyMetricFactory(factory.django.DjangoModelFactory):
    """Параметр тела. По умолчанию общий — как у видов спорта и упражнений."""

    class Meta:
        model = BodyMetric

    name = factory.Sequence(lambda n: f"Параметр {n}")
    unit = "см"
    owner = None


class BodyMeasurementFactory(factory.django.DjangoModelFactory):
    """Замер: каждый следующий — на день раньше, чтобы не упереться в «один в день»."""

    class Meta:
        model = BodyMeasurement

    user = factory.SubFactory(UserFactory)
    metric = factory.SubFactory(BodyMetricFactory)
    value = Decimal("80")
    measured_on = factory.Sequence(lambda n: timezone.localdate() - timedelta(days=n))
