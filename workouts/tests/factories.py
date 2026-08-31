from decimal import Decimal

import factory
from django.utils import timezone

from accounts.tests.factories import UserFactory
from workouts.models import (
    CardioDetails,
    ChangelogEntry,
    Exercise,
    ExerciseNote,
    ExerciseSettings,
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


class CardioDetailsFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CardioDetails

    workout = factory.SubFactory(WorkoutFactory, sport__category=Sport.Category.CARDIO)
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
