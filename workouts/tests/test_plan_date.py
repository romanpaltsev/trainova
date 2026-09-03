"""Плановый день черновика: назначение, порядок в чузере, стирание при старте."""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import formats, timezone

from workouts.models import Sport, Workout
from workouts.tests.factories import SportFactory, StrengthSetFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def bike():
    return SportFactory(name="Велосипед", category=Sport.Category.CARDIO, owner=None)


@pytest.fixture
def draft(user):
    """Силовой черновик: у него дата ставится отдельным экраном, не формой."""
    planned = WorkoutFactory(user=user, started_at=None, duration_min=None)
    StrengthSetFactory(workout=planned, set_number=1, done=False)
    return planned


def test_cardio_plan_keeps_planned_day(client, user, bike):
    """У кардио день задаётся прямо в форме плана."""
    client.force_login(user)
    day = timezone.localdate() + timedelta(days=3)

    client.post(
        reverse("cardio_prepare"),
        {
            "sport": str(bike.pk),
            "distance_km": "30",
            "planned_for": day.isoformat(),
            "note": "",
            "location_own": "",
        },
    )

    assert Workout.objects.get(user=user).planned_for == day


def test_planned_day_may_be_in_the_future(client, user, bike):
    """В этом и смысл плана — в отличие от даты записи, которая в будущее нельзя."""
    client.force_login(user)
    future = timezone.localdate() + timedelta(days=30)

    client.post(
        reverse("cardio_prepare"),
        {
            "sport": str(bike.pk),
            "distance_km": "30",
            "planned_for": future.isoformat(),
            "note": "",
            "location_own": "",
        },
    )

    assert Workout.objects.get(user=user).planned_for == future


def test_recording_date_still_cannot_be_in_the_future(client, user, bike):
    """Послабление для плана не должно протечь в запись."""
    client.force_login(user)
    future = timezone.localdate() + timedelta(days=1)

    response = client.post(
        reverse("cardio_create"),
        {
            "sport": str(bike.pk),
            "date": future.isoformat(),
            "duration_minutes": "40",
            "distance_km": "10",
            "note": "",
            "location_own": "",
        },
    )

    assert response.status_code == 200
    assert "Дата не может быть в будущем." in response.content.decode()
    assert not Workout.objects.filter(user=user).exists()


def test_strength_draft_gets_day_through_modal(client, user, draft):
    """У силового черновика формы нет, день ставится отдельным эндпоинтом."""
    client.force_login(user)
    day = timezone.localdate() + timedelta(days=2)

    response = client.post(
        reverse("workout_planned_for", args=[draft.pk]), {"planned_for": day.isoformat()}
    )

    draft.refresh_from_db()
    assert response.status_code == 200
    assert draft.planned_for == day
    # Ответ — только OOB-значение: пустой остаток закрывает модалку.
    assert "hx-swap-oob" in response.content.decode()


def test_day_can_be_cleared(client, user, draft):
    """«Убрать день» — своя кнопка со своим именем, а не пустое поле."""
    draft.planned_for = timezone.localdate()
    draft.save(update_fields=["planned_for"])
    client.force_login(user)

    client.post(reverse("workout_planned_for", args=[draft.pk]), {"clear": "1"})

    draft.refresh_from_db()
    assert draft.planned_for is None


def test_garbage_day_clears_instead_of_500(client, user, draft):
    """Мусор уводит в пустоту, а не роняет страницу — как у фильтров истории."""
    client.force_login(user)

    response = client.post(reverse("workout_planned_for", args=[draft.pk]), {"planned_for": "abc"})

    draft.refresh_from_db()
    assert response.status_code == 200
    assert draft.planned_for is None


def test_started_workout_has_no_day_screen(client, user):
    """Идущая тренировка планового дня не имеет — эндпоинт её не принимает."""
    live = WorkoutFactory(user=user, duration_min=None)
    client.force_login(user)

    assert client.get(reverse("workout_planned_for", args=[live.pk])).status_code == 404


def test_starting_dated_draft_clears_the_day(client, user, draft):
    """Старт стирает плановый день тем же UPDATE, что проставляет начало.

    Отдельным запросом нельзя: между ними строка нарушала бы констрейнт, и
    «Начать тренировку» падало бы на любом датированном черновике.
    """
    draft.planned_for = timezone.localdate()
    draft.save(update_fields=["planned_for"])
    client.force_login(user)

    response = client.post(reverse("draft_start", args=[draft.pk]))

    draft.refresh_from_db()
    assert response.status_code == 302
    assert draft.started_at is not None
    assert draft.planned_for is None


def test_database_rejects_day_on_started_workout(user):
    """Констрейнт planned_for_only_when_planned — не декорация."""
    workout = WorkoutFactory(user=user)

    with pytest.raises(IntegrityError), transaction.atomic():
        Workout.objects.filter(pk=workout.pk).update(planned_for=timezone.localdate())


def test_recording_cardio_plan_clears_the_day(client, user, bike):
    """Записанной тренировке плановый день не нужен: настоящий день в started_at."""
    client.force_login(user)
    client.post(
        reverse("cardio_prepare"),
        {
            "sport": str(bike.pk),
            "distance_km": "30",
            "planned_for": timezone.localdate().isoformat(),
            "note": "",
            "location_own": "",
        },
    )
    plan = Workout.objects.get(user=user)

    client.post(
        reverse("workout_edit", args=[plan.pk]),
        {
            "sport": str(bike.pk),
            "date": timezone.localdate().isoformat(),
            "duration_minutes": "40",
            "distance_km": "30",
            "note": "",
            "location_own": "",
        },
    )

    plan.refresh_from_db()
    assert plan.is_finished
    assert plan.planned_for is None


def test_start_modal_orders_drafts_by_day(client, user):
    """Датированные планы идут по возрастанию дня, недатированные — после них."""
    today = timezone.localdate()
    later = WorkoutFactory(user=user, started_at=None, duration_min=None, sport__name="Позже")
    later.planned_for = today + timedelta(days=5)
    later.save(update_fields=["planned_for"])
    sooner = WorkoutFactory(user=user, started_at=None, duration_min=None, sport__name="Раньше")
    sooner.planned_for = today + timedelta(days=1)
    sooner.save(update_fields=["planned_for"])
    WorkoutFactory(user=user, started_at=None, duration_min=None, sport__name="Когда-нибудь")
    client.force_login(user)

    content = client.get(reverse("workout_start")).content.decode()

    assert content.index("Раньше") < content.index("Позже") < content.index("Когда-нибудь")


@pytest.mark.parametrize(
    ("offset", "expected"),
    [(0, "сегодня"), (1, "завтра"), (3, None), (30, None)],
    ids=["today", "tomorrow", "this-week", "far"],
)
def test_day_is_shown_shortly_in_start_modal(client, user, draft, offset, expected):
    """День виден в строке плана, и коротко: в неё же встают две цели.

    Ближайшие дни называются словами, остальная неделя — днём недели, дальше
    датой. Длинная форма «сб, 5 сен» в 375px вместе с целями не помещается.
    """
    day = timezone.localdate() + timedelta(days=offset)
    draft.planned_for = day
    draft.save(update_fields=["planned_for"])
    client.force_login(user)

    content = client.get(reverse("workout_start")).content.decode()

    if expected is None:
        expected = (
            formats.date_format(day, "D").lower() if offset < 7 else formats.date_format(day, "j b")
        )
    assert expected in content


def test_other_user_cannot_touch_the_day(client, user, other_user):
    """Изоляция: чужой черновик — 404 и на модалку, и на сохранение."""
    theirs = WorkoutFactory(user=other_user, started_at=None, duration_min=None)
    client.force_login(user)

    assert client.get(reverse("workout_planned_for", args=[theirs.pk])).status_code == 404
    response = client.post(
        reverse("workout_planned_for", args=[theirs.pk]),
        {"planned_for": timezone.localdate().isoformat()},
    )

    theirs.refresh_from_db()
    assert response.status_code == 404
    assert theirs.planned_for is None
