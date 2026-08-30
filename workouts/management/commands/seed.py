"""Наполнение глобальных справочников и стартовых новостей.

Команда идемпотентна: повторный запуск ничего не дублирует, так что её безопасно
вызывать после каждого деплоя. Ключ идемпотентности новостей — точный заголовок,
поэтому переименованную в админке запись команда создаст заново.

Новости здесь — только стартовые, чтобы на пустой базе экран «Что нового» не был
пустым. Анонсы релизов пишутся в Django admin: там можно поставить дату задним
числом или отложить публикацию, и для этого не нужен деплой.
"""

from datetime import date, datetime, time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from workouts.models import ChangelogEntry, Exercise, Sport

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

# Стартовые записи «Что нового»: (тип, заголовок, текст, дата публикации).
# Даты фиксированные, а не timezone.now(): команда должна давать одинаковый
# результат при каждом запуске. Время — полдень, как у тренировок за прошедший
# день: смещение часового пояса не сдвинет дату на соседний день.
CHANGELOG = [
    (
        ChangelogEntry.Kind.FEATURE,
        "Тёмная и светлая темы",
        "В профиле появился переключатель: светлая, тёмная или как в системе. "
        "Выбор запоминается на устройстве.",
        date(2026, 8, 25),
    ),
    (
        ChangelogEntry.Kind.FEATURE,
        "Повтор тренировки",
        "Кнопка «Повторить» в истории создаёт новую тренировку с тем же набором "
        "упражнений — веса подставятся из прошлого раза.",
        date(2026, 8, 18),
    ),
    (
        ChangelogEntry.Kind.FIX,
        "Таймер отдыха",
        "Таймер больше не сбрасывается, если экран телефона погас между подходами.",
        date(2026, 8, 12),
    ),
]


class Command(BaseCommand):
    help = "Создаёт глобальные виды спорта, базовые упражнения и новости (idempotent)"

    @transaction.atomic
    def handle(self, *args, **options):
        sports_created = self._seed_sports()
        exercises_created = self._seed_exercises()
        news_created = self._seed_changelog()

        self.stdout.write(
            self.style.SUCCESS(
                f"Виды спорта: создано {sports_created}, уже было {len(SPORTS) - sports_created}. "
                f"Упражнения: создано {exercises_created}, "
                f"уже было {len(EXERCISES) - exercises_created}. "
                f"Новости: создано {news_created}, уже было {len(CHANGELOG) - news_created}."
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

    def _seed_changelog(self):
        created = 0
        for kind, title, body, day in CHANGELOG:
            # Существующие записи не правим: их мог отредактировать админ.
            if ChangelogEntry.objects.filter(title=title).exists():
                continue
            ChangelogEntry.objects.create(
                kind=kind,
                title=title,
                body=body,
                published_at=timezone.make_aware(datetime.combine(day, time(12, 0))),
            )
            created += 1
        return created
