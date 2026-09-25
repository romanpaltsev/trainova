"""Кардио-тренировку можно подготовить заранее: план, его запись и изоляция."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.models import CardioPart, Sport, Workout
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
    assert workout.cardio_parts.get().distance_km == 30


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
    # Времени нет вместе с датой: без даты оно ничего не значит, а оставленное
    # поле размывало бы ту самую структурную гарантию.
    for absent in ("date", "time", "avg_heart_rate"):
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
    assert draft.cardio_parts.get().distance_km == Decimal("32.40")
    assert draft.cardio_parts.get().avg_heart_rate == 138
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
    CardioPart.objects.create(workout=theirs, sport=theirs.sport, distance_km=30)
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
    CardioPart.objects.create(workout=theirs, sport=theirs.sport, distance_km=77)
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
    """Цель только по времени: строки CardioPart при этом не появляется."""
    client.force_login(user)

    prepare(client, bike, distance_km="", duration_minutes="45")

    workout = Workout.objects.get(user=user)
    assert workout.target_duration_min == 45
    assert workout.is_planned
    assert not CardioPart.objects.filter(workout=workout).exists()


def test_prepare_without_any_target(client, user, bike):
    """Обе цели необязательны: пустой план — законная заготовка на неделю."""
    client.force_login(user)

    response = prepare(client, bike, distance_km="")

    workout = Workout.objects.get(user=user)
    assert response.status_code == 302
    assert workout.is_planned
    assert workout.target_duration_min is None
    assert not CardioPart.objects.filter(workout=workout).exists()


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


# ---------- Будущая дата предлагает подготовить план ----------
#
# Дата в будущем — не ошибка человека, а другое намерение: тренировки ещё не
# было, значит её готовят. Из тупика «Дата не может быть в будущем» должен быть
# выход, не теряющий введённого.


def record_data(sport, **overrides):
    """Поля формы записи — той самой, где и возникает тупик."""
    data = {
        "sport": str(sport.pk),
        "date": (timezone.localdate() + timedelta(days=3)).isoformat(),
        "time": "",
        "duration_hours": "1",
        "duration_minutes": "0",
        "distance_km": "30",
        "avg_heart_rate": "",
        "note": "",
        "location_own": "",
    }
    return data | overrides


def test_future_date_offers_to_prepare_that_day(client, user, bike):
    client.force_login(user)
    day = timezone.localdate() + timedelta(days=3)

    response = client.post(reverse("cardio_create"), record_data(bike))

    content = response.content.decode()
    assert response.context["form"].future_date == day
    assert reverse("cardio_prepare") in content
    assert f'value="{day.isoformat()}"' in content


def test_offer_carries_entered_values_into_the_plan(client, user, bike):
    """Кнопка шлёт ту же форму на маршрут плана — набирать заново нечего."""
    client.force_login(user)
    day = timezone.localdate() + timedelta(days=3)
    data = record_data(bike) | {"planned_for": day.isoformat()}

    client.post(reverse("cardio_prepare"), data)

    workout = Workout.objects.get(user=user)
    assert workout.is_planned
    assert workout.planned_for == day
    assert workout.cardio_parts.get().distance_km == 30
    assert workout.target_duration_min == 60
    # Форма плана полей date и time не имеет вовсе, поэтому дослать их
    # обходным путём нельзя — «планом нельзя записать тренировку» цело.
    assert workout.started_at is None
    assert workout.duration_min is None


def test_no_offer_for_a_valid_past_date(client, user, bike):
    client.force_login(user)
    yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()

    response = client.post(reverse("cardio_create"), record_data(bike, date=yesterday), follow=True)

    assert Workout.objects.get(user=user).is_finished
    assert reverse("cardio_prepare") not in response.content.decode()


@pytest.mark.parametrize(
    "state", [pytest.param("finished", id="edit"), pytest.param("draft", id="record-plan")]
)
def test_no_offer_when_a_workout_is_open(client, user, bike, state):
    """На правке кнопка создавала бы вторую тренировку вместо правки первой."""
    client.force_login(user)
    workout = WorkoutFactory(
        user=user,
        sport=bike,
        started_at=None if state == "draft" else timezone.now(),
        duration_min=None if state == "draft" else 60,
    )
    future = (timezone.localdate() + timedelta(days=3)).isoformat()

    response = client.post(
        reverse("workout_edit", args=[workout.pk]), record_data(bike, date=future)
    )

    assert response.context["form"].future_date is not None
    assert reverse("cardio_prepare") not in response.content.decode()
    assert Workout.objects.filter(user=user).count() == 1


def test_offer_survives_other_invalid_fields(client, user, bike):
    """Сломано что-то ещё — предложение остаётся, и план тоже не создаётся молча."""
    client.force_login(user)
    day = timezone.localdate() + timedelta(days=3)

    broken = record_data(bike, distance_km="abc")
    assert (
        reverse("cardio_prepare") in client.post(reverse("cardio_create"), broken).content.decode()
    )

    response = client.post(reverse("cardio_prepare"), broken | {"planned_for": day.isoformat()})

    assert "distance_km" in response.context["form"].errors
    assert not Workout.objects.exists()
