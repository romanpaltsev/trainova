"""Стиль хода у лыж: конёк или классика.

Стиль уточняет вид спорта («Лыжи · конёк»), подставляется из прошлой лыжной
части и делит рекорды: конёк заметно быстрее, и общий лучший темп навсегда
остался бы за ним. Бывает только у видов спорта из STYLED_SPORTS.
"""

import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from workouts import excel, excel_import, stats
from workouts.models import CardioPart, DeletedWorkout, Sport, Workout
from workouts.tests.factories import (
    CardioPartFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)
from workouts.tests.sheets import build_sheet

pytestmark = pytest.mark.django_db

SKATE = CardioPart.Style.SKATE
CLASSIC = CardioPart.Style.CLASSIC


@pytest.fixture
def ski():
    return SportFactory(name="Лыжи", category=Sport.Category.CARDIO, owner=None)


@pytest.fixture
def run():
    return SportFactory(name="Бег", category=Sport.Category.CARDIO, owner=None)


def recorded_part(user, sport, *, days_ago=0, minutes=40, **fields):
    workout = WorkoutFactory(
        user=user,
        sport=sport,
        started_at=timezone.now() - timedelta(days=days_ago),
        duration_min=minutes,
    )
    return CardioPartFactory(workout=workout, sport=sport, duration_min=minutes, **fields)


# ---------- Модель ----------


def test_styles_belong_to_skis_by_name(user, ski, run):
    rollers = SportFactory(name=" Лыжероллеры ", category=Sport.Category.CARDIO, owner=user)
    roller_skates = SportFactory(name="Роллеры", category=Sport.Category.CARDIO, owner=user)

    assert ski.has_styles and rollers.has_styles
    assert not run.has_styles and not roller_skates.has_styles


def test_title_carries_style(ski):
    assert CardioPart(sport=ski, style=SKATE).title == "Лыжи · конёк"
    assert CardioPart(sport=ski).title == "Лыжи"


def test_unknown_style_is_rejected_by_database(user, ski):
    part = recorded_part(user, ski)

    with pytest.raises(IntegrityError):
        CardioPart.objects.filter(pk=part.pk).update(style="free")


# ---------- Форма кардио ----------


def form_data(sport, **overrides):
    data = {
        "sport": sport.pk,
        "date": timezone.localdate().isoformat(),
        "duration_hours": 1,
        "duration_minutes": 10,
        "distance_km": "15.2",
        "style": SKATE,
        "note": "",
    }
    return data | overrides


def test_cardio_form_saves_style_of_skis(client, user, ski):
    client.force_login(user)

    client.post(reverse("cardio_create"), form_data(ski))

    assert CardioPart.objects.get().style == SKATE


def test_style_is_dropped_for_sport_without_styles(client, user, run):
    """Выбрали конёк, потом сменили «Лыжи» на «Бег» — в пробежку он не едет."""
    client.force_login(user)

    client.post(reverse("cardio_create"), form_data(run))

    assert CardioPart.objects.get().style == ""


def test_unknown_style_is_a_form_error(client, user, ski):
    client.force_login(user)

    response = client.post(reverse("cardio_create"), form_data(ski, style="freestyle"))

    assert "Такого стиля нет." in response.content.decode()
    assert not Workout.objects.exists()


def test_plan_has_no_style(client, user, ski):
    client.force_login(user)

    content = client.get(reverse("cardio_prepare"), {"sport": ski.pk}).content.decode()
    client.post(reverse("cardio_prepare"), {"sport": ski.pk, "distance_km": "20", "style": SKATE})

    assert 'name="style"' not in content
    assert CardioPart.objects.get().style == ""


def test_new_form_from_chooser_gets_last_style(client, user, ski):
    recorded_part(user, ski, days_ago=5, style=SKATE)
    recorded_part(user, ski, days_ago=1, style=CLASSIC)
    recorded_part(user, ski, style="")  # без стиля не сбивает подстановку
    client.force_login(user)

    content = client.get(reverse("cardio_create"), {"sport": ski.pk}).content.decode()

    assert 'value="classic" class="app-chip-input" checked' in content
    assert reverse("cardio_style") in content


def test_edit_form_shows_style_without_prefill(client, user, ski):
    part = recorded_part(user, ski, style=SKATE)
    client.force_login(user)

    content = client.get(reverse("workout_edit", args=[part.workout.pk])).content.decode()

    assert 'value="skate" class="app-chip-input" checked' in content
    assert reverse("cardio_style") not in content


# ---------- Эндпоинт чипов ----------


def style_block(client, sport, **params):
    return client.get(reverse("cardio_style"), {"sport": sport.pk} | params)


def test_block_shows_chips_only_for_skis(client, user, ski, run):
    recorded_part(user, ski, style=SKATE)
    client.force_login(user)

    skis = style_block(client, ski).content.decode()
    running = style_block(client, run, style=SKATE, style_prefilled="1").content.decode()

    assert 'value="skate" class="app-chip-input" checked' in skis
    assert 'name="style"' not in running


def test_chosen_style_survives_switch_between_styled_sports(client, user, ski):
    rollers = SportFactory(name="Лыжероллеры", category=Sport.Category.CARDIO, owner=user)
    recorded_part(user, rollers, style=SKATE)
    client.force_login(user)

    assert style_block(client, rollers, style=CLASSIC).status_code == 204
    replaced = style_block(client, rollers, style=CLASSIC, style_prefilled="1")
    assert 'value="skate" class="app-chip-input" checked' in replaced.content.decode()


def test_others_and_drafts_do_not_give_style(client, user, other_user, ski):
    recorded_part(other_user, ski, style=SKATE)
    draft = WorkoutFactory(user=user, sport=ski, started_at=None, duration_min=None)
    CardioPartFactory(workout=draft, sport=ski, style=CLASSIC)
    client.force_login(user)

    content = style_block(client, ski).content.decode()

    assert "checked" not in content
    assert 'name="style"' in content


def test_style_endpoint_needs_login(client, ski):
    response = style_block(client, ski)

    assert response.status_code == 302


def test_new_own_sport_announces_itself_to_prefill_blocks(client, user):
    """Чип, подменённый после «+ Свой», сообщает о выборе: иначе стиль,
    тренажёр и маршрут остались бы от прошлого вида спорта."""
    client.force_login(user)

    content = client.post(
        reverse("sport_create"), {"name": "Лыжероллеры", "category": Sport.Category.CARDIO}
    ).content.decode()

    assert "dispatchEvent(new Event('change'" in content
    page = client.get(reverse("cardio_create")).content.decode()
    assert "dispatchEvent" not in page


# ---------- Окно «+ Кардио» ----------


def test_modal_saves_style_and_shows_it_on_edit(client, user, ski):
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    workout = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=workout)
    client.force_login(user)

    client.post(
        reverse("workout_cardio_part", args=[workout.pk]),
        {"sport": ski.pk, "style": CLASSIC, "duration_hours": 0, "duration_minutes": 20},
    )
    part = CardioPart.objects.get()
    content = client.get(
        reverse("workout_cardio_part_edit", args=[workout.pk, part.pk])
    ).content.decode()

    assert part.style == CLASSIC
    assert 'value="classic" class="app-chip-input" checked' in content
    summary = client.get(reverse("workout_summary", args=[workout.pk])).content.decode()
    assert "Лыжи · классика" in summary


# ---------- Подписи и рекорды ----------


def test_feed_and_latest_are_titled_with_style(client, user, ski, run):
    recorded_part(user, ski, style=SKATE)
    recorded_part(user, run, days_ago=1)
    client.force_login(user)

    history = client.get(reverse("workout_history")).content.decode()
    latest = stats.latest_workouts(user)

    assert "Лыжи · конёк" in history
    assert [row["sport_name"] for row in latest] == ["Лыжи · конёк", "Бег"]


def test_mixed_workout_title_keeps_sport_names(user, ski):
    """У смешанной подпись — группы мышц плюс виды спорта частей, без стиля:
    там «·» уже разделяет виды спорта."""
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    workout = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=workout, exercise__muscle_group="Пресс")
    CardioPartFactory(workout=workout, sport=ski, style=SKATE, duration_min=20)

    assert stats.latest_workouts(user)[0]["sport_name"] == "Пресс + Лыжи"


def test_records_are_split_by_style(user, ski):
    recorded_part(user, ski, minutes=60, distance_km=Decimal("20"), style=SKATE)
    recorded_part(user, ski, minutes=60, distance_km=Decimal("12"), style=CLASSIC)
    recorded_part(user, ski, minutes=60, distance_km=Decimal("10"))

    records = {row["name"]: row for row in stats.cardio_records(user)}

    assert list(records) == ["Лыжи", "Лыжи · классика", "Лыжи · конёк"]
    assert records["Лыжи · классика"]["distance_display"] == "12"
    assert records["Лыжи · конёк"]["metric_display"] == "20,0 км/ч"


def test_delete_page_and_trash_use_style_title(client, user, ski):
    part = recorded_part(user, ski, style=CLASSIC)
    client.force_login(user)

    page = client.get(reverse("workout_delete", args=[part.workout.pk])).content.decode()
    client.post(reverse("workout_delete", args=[part.workout.pk]))

    assert "Лыжи · классика" in page
    assert DeletedWorkout.objects.get().title == "Лыжи · классика"


def test_restore_brings_style_back(client, user, ski):
    part = recorded_part(user, ski, style=SKATE)
    client.force_login(user)

    client.post(reverse("workout_delete", args=[part.workout.pk]))
    client.post(reverse("workout_restore", args=[DeletedWorkout.objects.get().pk]))

    assert CardioPart.objects.get().style == SKATE


def test_history_query_budget_does_not_grow_with_styled_skis(client, user, ski):
    client.force_login(user)

    def queries():
        with CaptureQueriesContext(connection) as context:
            client.get(reverse("workout_history"))
        return len(context.captured_queries)

    recorded_part(user, ski, style=SKATE)
    one = queries()
    for days in range(1, 5):
        recorded_part(user, ski, days_ago=days, style=CLASSIC)
    assert queries() == one


# ---------- Excel ----------

PAST = date(2026, 9, 4)


def ski_row(**extra):
    return {"date": PAST, "sport": "Лыжи", "distance": 15, "duration": 70} | extra


def test_style_goes_to_excel_and_back(user, other_user, ski):
    part = recorded_part(user, ski, style=SKATE)
    part.workout.started_at = timezone.make_aware(datetime.combine(PAST, time(10, 0)))
    part.workout.save(update_fields=["started_at"])
    buffer = io.BytesIO()
    excel.build_workbook(user).save(buffer)
    buffer.seek(0)

    report = excel_import.import_workbook(other_user, buffer)

    assert report.error_count == 0
    assert CardioPart.objects.get(workout__user=other_user).style == SKATE


@pytest.mark.parametrize("cell", ["Конек", "КЛАССИКА", "конёк"])
def test_style_cell_ignores_case_and_yo(user, ski, cell):
    report = excel_import.import_workbook(user, build_sheet([ski_row(style=cell)]))

    assert report.error_count == 0
    assert CardioPart.objects.get().style in (SKATE, CLASSIC)


def test_unknown_style_cell_is_a_row_error(user, ski):
    report = excel_import.import_workbook(user, build_sheet([ski_row(style="свободный")]))

    assert any("Стиль — «конёк» или «классика»." in line for line in report.errors)
    assert CardioPart.objects.get().style == ""


def test_style_of_running_is_a_warning(user, run):
    report = excel_import.import_workbook(user, build_sheet([ski_row(sport="Бег", style="конёк")]))

    assert CardioPart.objects.get().style == ""
    assert any("бывает только у лыж" in line for line in report.warnings)
