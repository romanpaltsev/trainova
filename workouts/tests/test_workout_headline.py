"""Подпись силовой тренировки по группам мышц: порядок, свёртка, фолбэк, изоляция."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts import stats
from workouts.models import Sport
from workouts.tests.factories import (
    CardioDetailsFactory,
    ExerciseFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def label_for(workout):
    """Подпись одной тренировки — так её получают все вьюхи."""
    return stats.muscle_groups_by_workout(workout.user, [workout.pk]).get(workout.pk, "")


def test_groups_follow_actual_order_not_order_added(user):
    """Порядок — по метке выполнения: сначала то, что делали раньше."""
    workout = WorkoutFactory(user=user)
    chest = ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")
    legs = ExerciseFactory(name="Приседания", muscle_group="Ноги")
    now = timezone.now()
    # Ноги добавили вторыми (id больше), но сделали первыми.
    StrengthSetFactory(workout=workout, exercise=chest, set_number=1, done_at=now)
    StrengthSetFactory(
        workout=workout, exercise=legs, set_number=1, done_at=now - timedelta(minutes=20)
    )

    assert label_for(workout) == "Ноги · Грудь"


def test_planned_draft_follows_order_added(user):
    """У черновика метки выполнения нет — подпись идёт по порядку добавления."""
    draft = WorkoutFactory(user=user, started_at=None, duration_min=None)
    back = ExerciseFactory(name="Тяга штанги", muscle_group="Спина")
    arms = ExerciseFactory(name="Молотки", muscle_group="Руки")
    StrengthSetFactory(workout=draft, exercise=back, set_number=1, done=False)
    StrengthSetFactory(workout=draft, exercise=arms, set_number=1, done=False)

    assert label_for(draft) == "Спина · Руки"


def test_repeated_group_is_not_duplicated(user):
    """Два упражнения одной группы — одна группа в подписи."""
    workout = WorkoutFactory(user=user)
    bench = ExerciseFactory(name="Жим лёжа", muscle_group="Грудь")
    flyes = ExerciseFactory(name="Разведение рук", muscle_group="Грудь")
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=2)
    StrengthSetFactory(workout=workout, exercise=flyes, set_number=1)

    assert label_for(workout) == "Грудь"


def test_extra_groups_collapse_into_counter(user):
    """Показываем две группы, остальные сворачиваются в «+N»."""
    workout = WorkoutFactory(user=user)
    now = timezone.now()
    for number, group in enumerate(("Грудь", "Плечи", "Руки", "Пресс")):
        StrengthSetFactory(
            workout=workout,
            exercise=ExerciseFactory(muscle_group=group),
            set_number=1,
            done_at=now + timedelta(minutes=number),
        )

    assert label_for(workout) == "Грудь · Плечи +2"


def test_exercise_without_muscle_group_is_skipped(user):
    """Группа необязательная: упражнение без неё в подпись не попадает."""
    workout = WorkoutFactory(user=user)
    now = timezone.now()
    StrengthSetFactory(
        workout=workout,
        exercise=ExerciseFactory(muscle_group="Спина"),
        set_number=1,
        done_at=now,
    )
    StrengthSetFactory(
        workout=workout,
        exercise=ExerciseFactory(muscle_group=""),
        set_number=1,
        done_at=now + timedelta(minutes=5),
    )

    assert label_for(workout) == "Спина"


def test_workout_without_any_group_has_no_label(user):
    """Ни одной группы — подписи нет, и шаблон покажет имя вида спорта."""
    workout = WorkoutFactory(user=user)
    StrengthSetFactory(workout=workout, exercise=ExerciseFactory(muscle_group=""), set_number=1)

    assert label_for(workout) == ""


def test_cardio_workout_has_no_label(user):
    """У кардио подходов нет, значит и групп мышц не бывает."""
    cardio = CardioDetailsFactory(workout__user=user).workout

    assert label_for(cardio) == ""


def test_other_users_sets_do_not_leak_into_label(user, other_user):
    """Изоляция: подходы чужой тренировки в подпись своей не попадают."""
    mine = WorkoutFactory(user=user)
    StrengthSetFactory(workout=mine, exercise=ExerciseFactory(muscle_group="Грудь"), set_number=1)
    theirs = WorkoutFactory(user=other_user)
    StrengthSetFactory(workout=theirs, exercise=ExerciseFactory(muscle_group="Ноги"), set_number=1)

    # Чужой id подсунут явно — функция обязана его отбросить, а не поверить вызову.
    labels = stats.muscle_groups_by_workout(user, [mine.pk, theirs.pk])

    assert labels == {mine.pk: "Грудь"}


def test_attach_muscle_groups_sets_empty_label_for_every_workout(user):
    """Атрибут появляется у всех, иначе шаблон падал бы на части карточек."""
    with_group = WorkoutFactory(user=user)
    StrengthSetFactory(
        workout=with_group, exercise=ExerciseFactory(muscle_group="Пресс"), set_number=1
    )
    without_group = WorkoutFactory(user=user)

    rows = stats.attach_muscle_groups(user, [with_group, without_group])

    assert [row.muscle_groups for row in rows] == ["Пресс", ""]


def test_history_card_shows_groups_instead_of_sport_name(client, user):
    """Главный сценарий: в ленте вместо «Силовая» стоят группы мышц."""
    workout = WorkoutFactory(user=user, sport__name="Силовая")
    now = timezone.now()
    StrengthSetFactory(
        workout=workout, exercise=ExerciseFactory(muscle_group="Грудь"), set_number=1, done_at=now
    )
    StrengthSetFactory(
        workout=workout,
        exercise=ExerciseFactory(muscle_group="Плечи"),
        set_number=1,
        done_at=now + timedelta(minutes=10),
    )

    client.force_login(user)
    content = client.get(reverse("workout_history")).content.decode()

    assert "Грудь · Плечи" in content
    # Имя вида спорта остаётся в чипе фильтра, но не в карточке.
    assert "Силовая" in content


def test_history_card_falls_back_to_sport_name(client, user):
    """Тренировка без размеченных упражнений подписана как раньше."""
    WorkoutFactory(user=user, sport__name="Силовая")

    client.force_login(user)
    content = client.get(reverse("workout_history")).content.decode()

    assert "Силовая" in content


def test_cardio_card_keeps_sport_name(client, user):
    """Кардио-карточка подписана видом спорта, а не пустотой."""
    CardioDetailsFactory(
        workout__user=user,
        workout__sport__name="Велосипед",
        workout__sport__category=Sport.Category.CARDIO,
    )

    client.force_login(user)
    content = client.get(reverse("workout_history")).content.decode()

    assert "Велосипед" in content
