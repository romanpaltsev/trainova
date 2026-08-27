import factory
from django.utils import timezone

from accounts.tests.factories import UserFactory
from workouts.models import CardioDetails, Exercise, Sport, StrengthSet, Workout


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


class CardioDetailsFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CardioDetails

    workout = factory.SubFactory(WorkoutFactory, sport__category=Sport.Category.CARDIO)
    distance_km = 10
    avg_heart_rate = 140
