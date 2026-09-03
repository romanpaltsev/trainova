"""Кардио-тренировку можно подготовить заранее: план, его запись и изоляция."""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.models import CardioDetails, Sport, Workout
from workouts.tests.factories import SportFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def bike():
    return SportFactory(name="Велосипед", category=Sport.Category.CARDIO, owner=None)


def prepare(client, sport, **extra):
    """Подготовить кардио-тренировку так, как это делает форма плана."""
    data = {"sport": str(sport.pk), "distance_km": "30", "note": "", "location_own": ""}
    data.update(extra)
    return client.post(reverse("cardio_prepare"), data)


def test_prepare_creates_draft_with_target(client, user, bike):
    """План — это тренировка без начала и длительности плюс цель по дистанции."""
    client.force_login(user)

    response = prepare(client, bike)

    workout = Workout.objects.get(user=user)
    assert response.status_code == 302
    assert workout.started_at is None
    assert workout.duration_min is None
    assert workout.is_planned
    assert workout.cardio.distance_km == 30


def test_prepare_form_has_no_date_and_pulse(client, user, bike):
    """Даты записи и пульса в форме плана нет — и это не косметика.

    Без поля `date` форма физически не может вычислить started_at, поэтому
    «планом нельзя записать тренировку» держится структурой. А вот поля
    длительности у плана есть: там они значат цель по времени.
    """
    client.force_login(user)

    form = client.get(reverse("cardio_prepare")).context["form"]

    for present in ("distance_km", "duration_hours", "duration_minutes", "planned_for"):
        assert present in form.fields
    for absent in ("date", "avg_heart_rate"):
        assert absent not in form.fields


def test_prepare_duration_becomes_target_not_fact(client, user, bike):
    """Длительность в форме плана — это цель, а не факт.

    Дослать `date` руками бесполезно: поля в форме нет, значит started_at
    вычислить не из чего, и записать тренировку через план по-прежнему нельзя.
    """
    client.force_login(user)

    prepare(client, bike, duration_hours="1", duration_minutes="30", date="2026-09-01")

    workout = Workout.objects.get(user=user)
    assert workout.duration_min is None
    assert workout.started_at is None
    assert workout.target_duration_min == 90


def test_several_cardio_drafts_allowed(client, user, bike):
    """Черновиков сколько угодно: уникальный индекс требует начала."""
    client.force_login(user)

    prepare(client, bike, distance_km="30")
    prepare(client, bike, distance_km="45")

    assert Workout.objects.filter(user=user).planned().count() == 2


def test_draft_shows_target_in_start_modal(client, user, bike):
    """В чузере кардио-план подписан целью, а ведёт в форму записи."""
    client.force_login(user)
    prepare(client, bike, distance_km="30")
    draft = Workout.objects.get(user=user)

    content = client.get(reverse("workout_start")).content.decode()

    assert "30 км" in content
    assert reverse("workout_edit", args=[draft.pk]) in content
    # Живого режима у кардио нет — ссылки туда быть не должно.
    assert reverse("workout_live", args=[draft.pk]) not in content


def test_opening_draft_prefills_target_and_today(client, user, bike):
    """Открытие плана на запись не падает на пустых started_at и duration_min."""
    client.force_login(user)
    prepare(client, bike, distance_km="30")
    draft = Workout.objects.get(user=user)

    form = client.get(reverse("workout_edit", args=[draft.pk])).context["form"]

    assert form.initial["distance_km"] == 30
    assert form.initial["date"] == timezone.localdate()
    assert form.initial["duration_hours"] is None
    assert form.initial["duration_minutes"] is None


def test_recording_draft_turns_it_into_finished_workout(client, user, bike):
    """Запись плана — та же форма: появляются дата, длительность и факт дистанции."""
    client.force_login(user)
    prepare(client, bike, distance_km="30")
    draft = Workout.objects.get(user=user)
    today = timezone.localdate()

    response = client.post(
        reverse("workout_edit", args=[draft.pk]),
        {
            "sport": str(bike.pk),
            "date": today.isoformat(),
            "duration_hours": "1",
            "duration_minutes": "20",
            "distance_km": "32.4",
            "avg_heart_rate": "138",
            "note": "",
            "location_own": "",
        },
    )

    draft.refresh_from_db()
    assert response.status_code == 302
    assert draft.is_finished
    assert draft.duration_min == 80
    assert draft.started_at is not None
    # Цель заменилась фактом — это одно и то же поле, как вес у подхода.
    assert draft.cardio.distance_km == Decimal("32.40")
    assert draft.cardio.avg_heart_rate == 138
    assert Workout.objects.filter(user=user).count() == 1


def test_draft_is_not_in_history(client, user, bike):
    """План не записан, значит в ленту не попадает."""
    client.force_login(user)
    prepare(client, bike, distance_km="30")

    assert client.get(reverse("workout_history")).context["workouts"] == []


def test_cardio_draft_has_no_live_screen(client, user, bike):
    """Живой режим только у силовых — кардио-план туда не пускают."""
    client.force_login(user)
    prepare(client, bike, distance_km="30")
    draft = Workout.objects.get(user=user)

    assert client.get(reverse("workout_live", args=[draft.pk])).status_code == 404


def test_delete_page_shows_target(client, user, bike):
    """Подзаголовок подтверждения — цель: даты у плана нет."""
    client.force_login(user)
    prepare(client, bike, distance_km="30")
    draft = Workout.objects.get(user=user)

    content = client.get(reverse("workout_delete", args=[draft.pk])).content.decode()

    assert "30 км" in content


def test_other_user_cannot_open_or_record_draft(client, user, other_user, bike):
    """Изоляция: чужой план по прямому адресу — 404, и записать его нельзя."""
    theirs = WorkoutFactory(user=other_user, sport=bike, started_at=None, duration_min=None)
    CardioDetails.objects.create(workout=theirs, distance_km=30)
    client.force_login(user)

    assert client.get(reverse("workout_edit", args=[theirs.pk])).status_code == 404
    response = client.post(
        reverse("workout_edit", args=[theirs.pk]),
        {
            "sport": str(bike.pk),
            "date": timezone.localdate().isoformat(),
            "duration_hours": "1",
            "duration_minutes": "0",
            "distance_km": "10",
            "note": "",
            "location_own": "",
        },
    )
    theirs.refresh_from_db()
    assert response.status_code == 404
    assert theirs.is_planned


def test_other_users_draft_not_in_start_modal(client, user, other_user, bike):
    """Чужой план не виден в чузере.

    Проверяем адрес чужого черновика и его подпись, а не голое «77»: id вида
    спорта в разметке встречается несколько раз, и подстрока из двух цифр рано
    или поздно совпадёт с чем-нибудь посторонним.
    """
    theirs = WorkoutFactory(user=other_user, sport=bike, started_at=None, duration_min=None)
    CardioDetails.objects.create(workout=theirs, distance_km=77)
    client.force_login(user)

    content = client.get(reverse("workout_start")).content.decode()

    assert reverse("workout_edit", args=[theirs.pk]) not in content
    assert "77 км" not in content
    assert "Подготовлено" not in content


def test_strength_sport_rejected_by_prepare(client, user):
    """Подготовка кардио не должна принимать силовой вид спорта."""
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH, owner=None)
    client.force_login(user)

    response = prepare(client, strength)

    assert response.status_code == 200
    assert Workout.objects.filter(user=user).count() == 0


def test_prepare_with_time_target_only(client, user, bike):
    """Цель только по времени: строки CardioDetails при этом не появляется."""
    client.force_login(user)

    prepare(client, bike, distance_km="", duration_minutes="45")

    workout = Workout.objects.get(user=user)
    assert workout.target_duration_min == 45
    assert workout.is_planned
    assert not CardioDetails.objects.filter(workout=workout).exists()


def test_prepare_without_any_target(client, user, bike):
    """Обе цели необязательны: пустой план — законная заготовка на неделю."""
    client.force_login(user)

    response = prepare(client, bike, distance_km="")

    workout = Workout.objects.get(user=user)
    assert response.status_code == 302
    assert workout.is_planned
    assert workout.target_duration_min is None
    assert not CardioDetails.objects.filter(workout=workout).exists()


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"distance_km": "30", "duration_hours": "1", "duration_minutes": "20"}, "30 км · 1:20"),
        ({"distance_km": "30"}, "30 км"),
        ({"distance_km": "", "duration_minutes": "45"}, "0:45"),
        ({"distance_km": ""}, "пусто"),
    ],
    ids=["both", "distance-only", "time-only", "nothing"],
)
def test_plan_label_combines_targets(client, user, bike, fields, expected):
    """Подпись в чузере собирается из того, что задано."""
    client.force_login(user)
    prepare(client, bike, **fields)

    content = client.get(reverse("workout_start")).content.decode()

    assert expected in content


def test_opening_plan_prefills_time_target(client, user, bike):
    """Цель по времени подставляется в поля длительности — как цель по дистанции."""
    client.force_login(user)
    prepare(client, bike, duration_hours="1", duration_minutes="20")
    draft = Workout.objects.get(user=user)

    form = client.get(reverse("workout_edit", args=[draft.pk])).context["form"]

    assert form.initial["duration_hours"] == 1
    assert form.initial["duration_minutes"] == 20


def test_recording_plan_clears_time_target(client, user, bike):
    """Цель стала фактом: держать обе значило бы завести «план vs факт»."""
    client.force_login(user)
    prepare(client, bike, distance_km="30", duration_hours="1", duration_minutes="20")
    draft = Workout.objects.get(user=user)

    client.post(
        reverse("workout_edit", args=[draft.pk]),
        {
            "sport": str(bike.pk),
            "date": timezone.localdate().isoformat(),
            "duration_hours": "1",
            "duration_minutes": "35",
            "distance_km": "32",
            "note": "",
            "location_own": "",
        },
    )

    draft.refresh_from_db()
    assert draft.duration_min == 95
    assert draft.target_duration_min is None
