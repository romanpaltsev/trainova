"""Наполнение глобальных справочников.

Команда идемпотентна: повторный запуск ничего не дублирует, так что её безопасно
вызывать после каждого деплоя.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from workouts.models import Exercise, Sport

# Глобальные виды спорта: (название, категория)
SPORTS = [
    ("Силовая", Sport.Category.STRENGTH),
    ("Велосипед", Sport.Category.CARDIO),
    ("Бег", Sport.Category.CARDIO),
    ("Лыжи", Sport.Category.CARDIO),
]

# Базовые упражнения зала: (название, группа мышц). Список можно смело править.
EXERCISES = [
    ("Жим лёжа", "Грудь"),
    ("Жим лёжа в наклоне", "Грудь"),
    ("Разведение рук с гантелями", "Грудь"),
    ("Отжимания на брусьях", "Грудь"),
    ("Приседания со штангой", "Ноги"),
    ("Жим ногами", "Ноги"),
    ("Выпады с гантелями", "Ноги"),
    ("Разгибание ног в тренажёре", "Ноги"),
    ("Сгибание ног в тренажёре", "Ноги"),
    ("Подъёмы на носки", "Ноги"),
    ("Становая тяга", "Спина"),
    ("Тяга штанги в наклоне", "Спина"),
    ("Тяга верхнего блока", "Спина"),
    ("Тяга горизонтального блока", "Спина"),
    ("Подтягивания", "Спина"),
    ("Гиперэкстензия", "Спина"),
    ("Жим стоя", "Плечи"),
    ("Подъём гантелей через стороны", "Плечи"),
    ("Тяга штанги к подбородку", "Плечи"),
    ("Сгибания на бицепс со штангой", "Руки"),
    ("Молотки с гантелями", "Руки"),
    ("Французский жим", "Руки"),
    ("Скручивания", "Пресс"),
    ("Планка", "Пресс"),
]


class Command(BaseCommand):
    help = "Создаёт глобальные виды спорта и базовые упражнения (idempotent)"

    @transaction.atomic
    def handle(self, *args, **options):
        sports_created = self._seed_sports()
        exercises_created = self._seed_exercises()

        self.stdout.write(
            self.style.SUCCESS(
                f"Виды спорта: создано {sports_created}, уже было {len(SPORTS) - sports_created}. "
                f"Упражнения: создано {exercises_created}, "
                f"уже было {len(EXERCISES) - exercises_created}."
            )
        )

    def _seed_sports(self):
        created = 0
        for name, category in SPORTS:
            # Ищем без учёта регистра — так же, как ограничение уникальности в БД.
            sport = Sport.objects.global_only().filter(name__iexact=name).first()
            if sport is None:
                Sport.objects.create(name=name, category=category, owner=None)
                created += 1
            elif sport.category != category:
                sport.category = category
                sport.save(update_fields=["category"])
        return created

    def _seed_exercises(self):
        created = 0
        for name, muscle_group in EXERCISES:
            exercise = Exercise.objects.global_only().filter(name__iexact=name).first()
            if exercise is None:
                Exercise.objects.create(name=name, muscle_group=muscle_group, owner=None)
                created += 1
        return created
