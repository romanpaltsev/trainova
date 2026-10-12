"""Моё снаряжение: кроссовки, велосипед, лыжи — с пробегом.

Снаряжение — своя вещь, а не запись справочника: заводится вводом названия,
подставляется из прошлой части этого вида спорта (сначала из этого места),
копит пробег из дистанций записанных тренировок и уходит «на пенсию», а не
удаляется. Ссылка части держит вещь через RESTRICT.
"""

import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.models.deletion import RestrictedError
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.deletion import delete_user_data
from accounts.tests.factories import UserFactory
from workouts import excel, excel_import, services
from workouts.models import CardioPart, DeletedWorkout, Gear, Sport, Workout
from workouts.tests.factories import (
    CardioPartFactory,
    GearFactory,
    LocationFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)
from workouts.tests.sheets import build_sheet

pytestmark = pytest.mark.django_db


@pytest.fixture
def run():
    return SportFactory(name="Бег", category=Sport.Category.CARDIO, owner=None)


@pytest.fixture
def ski():
    return SportFactory(name="Лыжи", category=Sport.Category.CARDIO, owner=None)


@pytest.fixture
def park(user):
    return LocationFactory(owner=user, name="Парк у реки")


def recorded_part(user, sport, location=None, *, days_ago=0, **fields):
    workout = WorkoutFactory(
        user=user,
        sport=sport,
        location=location,
        started_at=timezone.now() - timedelta(days=days_ago),
        duration_min=40,
    )
    return CardioPartFactory(workout=workout, sport=sport, duration_min=40, **fields)


# ---------- Справочник и создание вводом ----------


def test_gear_for_name_finds_own_regardless_of_case(user, other_user):
    mine = GearFactory(owner=user, name="Novablast")
    GearFactory(owner=other_user, name="Pegasus")

    assert services.gear_for_name(user, "novablast") == mine
    created = services.gear_for_name(user, "Pegasus")
    assert (created.owner, created.name) == (user, "Pegasus")
    assert Gear.objects.filter(name__iexact="pegasus").count() == 2


def test_typed_gear_is_created_with_the_part(client, user, run):
    client.force_login(user)

    client.post(
        reverse("cardio_create"),
        {
            "sport": run.pk,
            "date": timezone.localdate().isoformat(),
            "duration_hours": 0,
            "duration_minutes": 40,
            "distance_km": "7.4",
            "gear_own": "  Кроссовки   Novablast ",
            "note": "",
        },
    )

    part = CardioPart.objects.get()
    assert part.gear.name == "Кроссовки Novablast"
    assert part.gear.owner == user


def test_failed_form_does_not_create_gear(client, user, run):
    client.force_login(user)

    client.post(reverse("cardio_create"), {"sport": run.pk, "gear_own": "Novablast"})

    assert not Gear.objects.exists()


def test_foreign_gear_id_is_rejected(client, user, other_user, run):
    theirs = GearFactory(owner=other_user)
    client.force_login(user)

    response = client.post(
        reverse("cardio_create"),
        {
            "sport": run.pk,
            "date": timezone.localdate().isoformat(),
            "duration_hours": 0,
            "duration_minutes": 40,
            "distance_km": "5",
            "gear": theirs.pk,
            "note": "",
        },
    )

    assert "Такого снаряжения у вас нет." in response.content.decode()
    assert not Workout.objects.exists()


def test_retired_gear_is_hidden_from_new_parts_but_kept_on_edit(client, user, run):
    shoes = GearFactory(owner=user, name="Старые кроссовки", retired_on=date(2026, 9, 1))
    part = recorded_part(user, run, gear=shoes)
    client.force_login(user)

    new = client.get(reverse("cardio_create"), {"sport": run.pk}).content.decode()
    edit = client.get(reverse("workout_edit", args=[part.workout.pk])).content.decode()

    assert "Старые кроссовки" not in new
    assert f'value="{shoes.pk}" class="app-chip-input" checked' in edit


def test_plan_has_no_gear(client, user, run):
    GearFactory(owner=user, name="Novablast")
    client.force_login(user)

    content = client.get(reverse("cardio_prepare"), {"sport": run.pk}).content.decode()

    assert 'name="gear"' not in content
    assert "Novablast" not in content


# ---------- Подстановка ----------


def test_prefill_prefers_this_place_then_anywhere(user, run, park):
    trail = GearFactory(owner=user, name="Трейловые")
    road = GearFactory(owner=user, name="Шоссейные")
    recorded_part(user, run, park, days_ago=5, gear=trail)
    recorded_part(user, run, LocationFactory(owner=user), days_ago=1, gear=road)

    assert services.last_cardio_gear(user, run, park) == trail
    assert services.last_cardio_gear(user, run, LocationFactory(owner=user)) == road
    assert services.last_cardio_gear(user, run, park, anywhere=True) == road


def test_prefill_follows_ski_style(user, ski):
    skate = GearFactory(owner=user, name="Коньковые")
    classic = GearFactory(owner=user, name="Классические")
    recorded_part(user, ski, days_ago=3, style=CardioPart.Style.SKATE, gear=skate)
    recorded_part(user, ski, days_ago=1, style=CardioPart.Style.CLASSIC, gear=classic)

    assert services.last_cardio_gear(user, ski, None, CardioPart.Style.SKATE) == skate
    assert services.last_cardio_gear(user, ski, None, CardioPart.Style.CLASSIC) == classic


def test_prefill_skips_retired_others_and_drafts(user, other_user, run):
    retired = GearFactory(owner=user, retired_on=date(2026, 9, 1))
    recorded_part(user, run, gear=retired)
    theirs = GearFactory(owner=other_user)
    recorded_part(other_user, run, gear=theirs)
    draft = WorkoutFactory(user=user, sport=run, started_at=None, duration_min=None)
    CardioPartFactory(workout=draft, sport=run, gear=GearFactory(owner=user))

    assert services.last_cardio_gear(user, run, None) is None


def test_chips_put_gear_of_this_sport_first(user, run, ski):
    shoes = GearFactory(owner=user, name="Ю-кроссовки")
    skis = GearFactory(owner=user, name="А-лыжи")
    recorded_part(user, run, gear=shoes)
    recorded_part(user, ski, gear=skis)

    assert services.gear_choices(user, run) == [shoes, skis]
    assert services.gear_choices(user, ski) == [skis, shoes]


def test_new_form_from_chooser_checks_last_gear(client, user, run, park):
    park.is_default = True
    park.save(update_fields=["is_default"])
    shoes = GearFactory(owner=user, name="Novablast")
    recorded_part(user, run, park, gear=shoes)
    client.force_login(user)

    content = client.get(reverse("cardio_create"), {"sport": run.pk}).content.decode()

    assert f'value="{shoes.pk}" class="app-chip-input" checked' in content
    assert reverse("cardio_gear") in content


def gear_block(client, sport, location=None, **params):
    query = {"sport": sport.pk, "location": location.pk if location else ""} | params
    return client.get(reverse("cardio_gear"), query)


def test_gear_block_comes_with_last_gear_checked(client, user, run, park):
    shoes = GearFactory(owner=user, name="Novablast")
    recorded_part(user, run, park, gear=shoes)
    client.force_login(user)

    content = gear_block(client, run, park).content.decode()

    assert f'value="{shoes.pk}" class="app-chip-input" checked' in content


def test_touched_gear_is_kept(client, user, run, park):
    """«Без снаряжения» — тоже выбор: тронутый блок перезапрос не трогает."""
    recorded_part(user, run, park, gear=GearFactory(owner=user))
    client.force_login(user)

    assert gear_block(client, run, park, gear="", gear_touched="1").status_code == 204


def test_gear_block_is_isolated(client, user, other_user, run):
    theirs = GearFactory(owner=other_user, name="Чужие кроссовки")
    recorded_part(other_user, run, gear=theirs)
    client.force_login(user)

    content = gear_block(client, run).content.decode()

    assert "Чужие кроссовки" not in content
    assert 'name="gear_own"' in content


def test_gear_block_needs_login(client, run):
    assert gear_block(client, run).status_code == 302


def test_modal_saves_gear_and_shows_it_in_part_row(client, user, run):
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    workout = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=workout)
    shoes = GearFactory(owner=user, name="Novablast")
    client.force_login(user)

    client.post(
        reverse("workout_cardio_part", args=[workout.pk]),
        {"sport": run.pk, "gear": shoes.pk, "duration_hours": 0, "duration_minutes": 15},
    )
    summary = client.get(reverse("workout_summary", args=[workout.pk])).content.decode()

    assert CardioPart.objects.get().gear == shoes
    assert "Novablast" in summary


# ---------- Пробег ----------


def test_mileage_counts_recorded_parts_and_start(client, user, other_user, run):
    shoes = GearFactory(owner=user, name="Novablast", initial_km=Decimal("120.5"))
    recorded_part(user, run, gear=shoes, distance_km=Decimal("7.4"))
    recorded_part(user, run, days_ago=1, gear=shoes, distance_km=Decimal("10"))
    draft = WorkoutFactory(user=user, sport=run, started_at=None, duration_min=None)
    CardioPartFactory(workout=draft, sport=run, gear=shoes, distance_km=30)
    # Чужая часть с моей вещью невозможна через приложение — но если бы
    # случилась, считать её в мой пробег нельзя: фильтр внутри агрегата.
    recorded_part(other_user, run, gear=shoes, distance_km=100)
    client.force_login(user)

    content = client.get(reverse("my_gear")).content.decode()

    assert "137,9\u00a0км" in content
    assert "в 2 тренировках" in content


# ---------- Страница «Моё снаряжение» ----------


def test_page_lists_only_own_gear(client, user, other_user):
    GearFactory(owner=user, name="Мой велосипед")
    GearFactory(owner=other_user, name="Чужой велосипед")
    client.force_login(user)

    content = client.get(reverse("my_gear")).content.decode()

    assert "Мой велосипед" in content
    assert "Чужой велосипед" not in content


def test_add_gear_on_page(client, user):
    GearFactory(owner=user, name="Novablast")
    client.force_login(user)

    client.post(reverse("my_gear"), {"name": "Trek Domane"})
    response = client.post(reverse("my_gear"), {"name": "novablast"}, follow=True)

    assert set(Gear.objects.values_list("name", flat=True)) == {"Novablast", "Trek Domane"}
    assert "«Novablast» уже есть." in response.content.decode()


def test_retire_and_back(client, user):
    shoes = GearFactory(owner=user, name="Novablast")
    client.force_login(user)

    retired = client.post(reverse("gear_retire", args=[shoes.pk]), {"retired": "1"})
    shoes.refresh_from_db()
    assert shoes.retired_on == timezone.localdate()
    assert "На пенсии" in retired.content.decode()
    # Повторное «на пенсию» из второй вкладки ничего не мигает обратно.
    client.post(reverse("gear_retire", args=[shoes.pk]), {"retired": "1"})
    shoes.refresh_from_db()
    assert shoes.is_retired

    client.post(reverse("gear_retire", args=[shoes.pk]), {"retired": "0"})
    shoes.refresh_from_db()
    assert shoes.retired_on is None


def test_edit_gear_name_and_start_mileage(client, user):
    shoes = GearFactory(owner=user, name="Novablst")
    GearFactory(owner=user, name="Pegasus")
    client.force_login(user)

    taken = client.post(reverse("gear_edit", args=[shoes.pk]), {"name": "pegasus"})
    saved = client.post(
        reverse("gear_edit", args=[shoes.pk]), {"name": "Novablast", "initial_km": "350,5"}
    )
    shoes.refresh_from_db()

    assert "Снаряжение с таким названием у вас уже есть." in taken.content.decode()
    assert (shoes.name, shoes.initial_km) == ("Novablast", Decimal("350.5"))
    assert 'hx-swap-oob="true"' in saved.content.decode()


def test_unused_gear_is_deleted_and_used_is_kept(client, user, run):
    unused = GearFactory(owner=user, name="Лишние")
    used = GearFactory(owner=user, name="Novablast")
    recorded_part(user, run, gear=used)
    client.force_login(user)

    client.post(reverse("gear_delete", args=[unused.pk]))
    response = client.post(reverse("gear_delete", args=[used.pk]), follow=True)

    assert list(Gear.objects.values_list("name", flat=True)) == ["Novablast"]
    assert "Отправьте на пенсию: пробег сохранится." in response.content.decode()


def test_delete_page_highlights_gear_in_sidebar(client, user):
    shoes = GearFactory(owner=user)
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    client.force_login(user)

    content = client.get(reverse("gear_delete", args=[shoes.pk])).content.decode()

    assert 'is-active" href="/profile/gear/"' in content


FOREIGN_ROUTES = [
    pytest.param("gear_edit", "get", id="edit-modal"),
    pytest.param("gear_edit", "post", id="edit"),
    pytest.param("gear_retire", "post", id="retire"),
    pytest.param("gear_delete", "get", id="delete-page"),
    pytest.param("gear_delete", "post", id="delete"),
]


@pytest.mark.parametrize(("url_name", "method"), FOREIGN_ROUTES)
def test_foreign_gear_routes_are_404(client, user, other_user, url_name, method):
    theirs = GearFactory(owner=other_user, name="Чужие кроссовки")
    client.force_login(user)

    response = getattr(client, method)(
        reverse(url_name, args=[theirs.pk]), {"name": "Взлом", "retired": "1"}
    )

    assert response.status_code == 404
    theirs.refresh_from_db()
    assert (theirs.name, theirs.retired_on) == ("Чужие кроссовки", None)


@pytest.mark.parametrize(("url_name", "method"), FOREIGN_ROUTES)
def test_anonymous_cannot_manage_gear(client, user, url_name, method):
    shoes = GearFactory(owner=user, name="Novablast")

    response = getattr(client, method)(reverse(url_name, args=[shoes.pk]), {"name": "Взлом"})

    assert response.status_code == 302
    shoes.refresh_from_db()
    assert shoes.name == "Novablast"


def test_page_query_budget_does_not_grow_with_gear(client, user, run):
    client.force_login(user)

    def queries():
        with CaptureQueriesContext(connection) as context:
            client.get(reverse("my_gear"))
        return len(context.captured_queries)

    recorded_part(user, run, gear=GearFactory(owner=user))
    one = queries()
    for days in range(1, 5):
        recorded_part(user, run, days_ago=days, gear=GearFactory(owner=user))
    assert queries() == one


def test_cardio_form_query_budget_does_not_grow_with_gear(client, user, run):
    client.force_login(user)

    def queries():
        with CaptureQueriesContext(connection) as context:
            client.get(reverse("cardio_create"), {"sport": run.pk})
        return len(context.captured_queries)

    GearFactory(owner=user)
    one = queries()
    for _ in range(4):
        GearFactory(owner=user)
    assert queries() == one


# ---------- RESTRICT и удаление аккаунта ----------


def test_used_gear_is_restricted_in_database(user, run):
    shoes = GearFactory(owner=user)
    recorded_part(user, run, gear=shoes)

    with pytest.raises(RestrictedError):
        shoes.delete()


@pytest.mark.parametrize("remove", [lambda user: user.delete(), delete_user_data])
def test_account_with_used_gear_is_deleted(user, run, remove):
    """RESTRICT пропускает строки того же каскада — PROTECT здесь упал бы."""
    recorded_part(user, run, gear=GearFactory(owner=user))

    remove(user)

    assert not Gear.objects.exists()
    assert not CardioPart.objects.exists()


# ---------- Профиль ----------


def test_profile_row_counts_gear_in_use(client, user):
    GearFactory(owner=user)
    GearFactory(owner=user)
    GearFactory(owner=user, retired_on=date(2026, 9, 1))
    client.force_login(user)

    content = client.get(reverse("profile")).content.decode()
    # Строка профиля, а не пункт боковой панели — у той ссылки свой класс.
    row = content.split('class="app-row" href="/profile/gear/"', 1)[1].split("</a>", 1)[0]

    assert "Моё снаряжение" in row
    assert '<span class="app-row-count num">2</span>' in row


# ---------- Корзина ----------


def test_restore_finds_renamed_gear_by_id_and_recreates_deleted(client, user, run):
    kept = GearFactory(owner=user, name="Novablast")
    gone = GearFactory(owner=user, name="Pegasus")
    first = recorded_part(user, run, gear=kept)
    second = recorded_part(user, run, days_ago=1, gear=gone)
    client.force_login(user)

    client.post(reverse("workout_delete", args=[first.workout.pk]))
    client.post(reverse("workout_delete", args=[second.workout.pk]))
    kept.name = "Novablast 5"
    kept.save(update_fields=["name"])
    gone.delete()
    for entry in DeletedWorkout.objects.all():
        client.post(reverse("workout_restore", args=[entry.pk]))

    names = sorted(CardioPart.objects.values_list("gear__name", flat=True))
    assert names == ["Novablast 5", "Pegasus"]
    assert CardioPart.objects.get(gear__name="Novablast 5").gear_id == kept.pk


# ---------- Excel ----------


def test_gear_goes_to_excel_and_back(user, other_user, run):
    GearFactory(owner=other_user, name="Novablast", initial_km=500)
    part = recorded_part(user, run, gear=GearFactory(owner=user, name="Novablast"))
    part.workout.started_at = timezone.make_aware(datetime.combine(date(2026, 9, 4), time(8, 0)))
    part.workout.save(update_fields=["started_at"])
    buffer = io.BytesIO()
    excel.build_workbook(user).save(buffer)
    buffer.seek(0)
    twin = UserFactory()

    report = excel_import.import_workbook(twin, buffer)

    imported = CardioPart.objects.get(workout__user=twin)
    assert report.created_gear == ["Novablast"]
    assert (imported.gear.name, imported.gear.owner) == ("Novablast", twin)


def test_import_reuses_own_gear_by_name(user, run):
    shoes = GearFactory(owner=user, name="Novablast")
    rows = [
        {
            "date": date(2026, 9, 4),
            "sport": "Бег",
            "duration": 40,
            "distance": 7,
            "gear": "novablast",
        },
        {
            "date": date(2026, 9, 5),
            "sport": "Бег",
            "duration": 40,
            "distance": 7,
            "gear": "Novablast",
        },
    ]

    report = excel_import.import_workbook(user, build_sheet(rows))

    assert report.created_gear == []
    assert set(CardioPart.objects.values_list("gear", flat=True)) == {shoes.pk}
