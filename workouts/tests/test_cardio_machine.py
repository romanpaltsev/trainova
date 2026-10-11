"""Тренажёр кардио: производитель, модель, уклон и уровень нагрузки.

Хранятся у самой кардио-части — это часть записи занятия, как дистанция.
Форма подставляет прошлое по виду спорта и месту, справочник тренажёров общий
с силовой, удаление используемой записи держит RESTRICT.
"""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from workouts.models import CardioPart, DeletedWorkout, MachineBrand, MachineModel, Sport
from workouts.tests.factories import (
    CardioPartFactory,
    LocationFactory,
    MachineBrandFactory,
    MachineModelFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def run():
    return SportFactory(name="Бег", category=Sport.Category.CARDIO, owner=None)


@pytest.fixture
def gym(user):
    return LocationFactory(owner=user, name="СпортЛайф")


def form_data(sport, **overrides):
    data = {
        "sport": sport.pk,
        "date": timezone.localdate().isoformat(),
        "duration_hours": 0,
        "duration_minutes": 40,
        "distance_km": "5",
        "avg_heart_rate": "",
        "note": "",
        "machine_brand": "",
        "machine_model": "",
        "incline_pct": "",
        "level": "",
    }
    return data | overrides


def fields_of(html):
    """Разметка блока до подсказок: в <datalist> видимые названия есть всегда."""
    return html.split('<datalist id="cardio-brands"')[0]


def recorded_part(user, sport, location, **fields):
    workout = WorkoutFactory(user=user, sport=sport, location=location, duration_min=40)
    return CardioPartFactory(workout=workout, sport=sport, duration_min=40, **fields)


def test_cardio_workout_records_machine_and_settings(client, user, run):
    """Совпавшее имя — общий производитель; незнакомая модель заводится своей."""
    technogym = MachineBrandFactory(name="Technogym")
    client.force_login(user)

    client.post(
        reverse("cardio_create"),
        form_data(
            run,
            machine_brand=" technogym ",
            machine_model="Run 1000",
            incline_pct="2,5",
            level="8",
        ),
    )

    part = CardioPart.objects.get()
    assert part.brand == technogym
    assert (part.model.name, part.model.owner, part.model.brand) == ("Run 1000", user, technogym)
    assert part.incline_pct == Decimal("2.5")
    assert part.level == 8
    assert part.machine_line == "Technogym · Run 1000 · уклон 2,5 % · уровень 8"


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"machine_model": "Run 1000"}, "Укажите производителя модели."),
        ({"incline_pct": "55"}, "Уклон — от −10 до 40 %."),
        ({"incline_pct": "круто"}, "Уклон — это число, например 2,5."),
        ({"level": "0"}, "Уровень — от 1 до 100."),
    ],
)
def test_invalid_machine_fields_are_errors(client, user, run, overrides, error):
    client.force_login(user)

    response = client.post(reverse("cardio_create"), form_data(run, **overrides))

    assert error in response.content.decode()
    assert not CardioPart.objects.exists()
    assert not MachineModel.objects.exists()


def test_edit_form_shows_and_clears_machine(client, user, run):
    brand = MachineBrandFactory(name="Kettler", owner=user)
    part = recorded_part(user, run, None, brand=brand, incline_pct=Decimal("3"), level=5)
    url = reverse("workout_edit", args=[part.workout.pk])
    client.force_login(user)

    page = client.get(url).content.decode()
    assert 'value="Kettler"' in fields_of(page)
    assert "Как в прошлый раз" not in page

    client.post(url, form_data(run))

    part.refresh_from_db()
    assert (part.brand, part.incline_pct, part.level) == (None, None, None)


def test_mixed_workout_part_gets_machine_and_shows_it(client, user, run):
    strength = SportFactory(name="Силовая", category=Sport.Category.STRENGTH)
    workout = WorkoutFactory(user=user, sport=strength, duration_min=60)
    StrengthSetFactory(workout=workout, set_number=1)
    client.force_login(user)

    client.post(
        reverse("workout_cardio_part", args=[workout.pk]),
        {
            "sport": run.pk,
            "distance_km": "3",
            "duration_hours": "0",
            "duration_minutes": "20",
            "machine_brand": "Life Fitness",
            "incline_pct": "5",
        },
    )

    part = workout.cardio_parts.get()
    assert part.brand.owner == user
    summary = client.get(reverse("workout_summary", args=[workout.pk])).content.decode()
    assert "Life Fitness · уклон 5 %" in summary


def test_plan_keeps_machine_without_distance(client, user, run):
    """«На дорожке, уклон 5» без километров — законная заготовка плана."""
    client.force_login(user)

    client.post(
        reverse("cardio_prepare"),
        {"sport": run.pk, "distance_km": "", "incline_pct": "5", "note": ""},
    )

    assert CardioPart.objects.get().incline_pct == Decimal("5")


# ---------- Подстановка прошлого ----------


def prefill(client, sport, location=None, **params):
    query = {"sport": sport.pk, "location": location.pk if location else ""} | params
    return client.get(reverse("cardio_machine"), query)


def test_prefill_takes_last_part_of_sport_in_same_place(client, user, other_user, run, gym):
    old = MachineBrandFactory(name="Старая дорожка")
    new = MachineBrandFactory(name="Новая дорожка")
    first = recorded_part(user, run, gym, brand=old, level=3)
    first.workout.started_at = timezone.now() - timezone.timedelta(days=3)
    first.workout.save(update_fields=["started_at"])
    recorded_part(user, run, gym, brand=new, incline_pct=Decimal("4"))
    recorded_part(user, run, None, brand=old)  # другое место
    recorded_part(other_user, run, None, brand=old)  # чужое
    client.force_login(user)

    fields = fields_of(prefill(client, run, gym).content.decode())

    assert 'value="Новая дорожка"' in fields
    assert 'value="4"' in fields
    assert "Как в прошлый раз" in fields
    other_sport = SportFactory(category=Sport.Category.CARDIO)
    fields = fields_of(prefill(client, other_sport, gym).content.decode())
    assert "дорожка" not in fields


def test_prefill_ignores_others_and_drafts(client, user, other_user, run):
    brand = MachineBrandFactory(name="Чужая дорожка")
    recorded_part(other_user, run, None, brand=brand)
    draft = WorkoutFactory(user=user, sport=run, started_at=None, duration_min=None)
    CardioPartFactory(workout=draft, sport=run, brand=brand)
    client.force_login(user)

    assert "Чужая дорожка" not in fields_of(prefill(client, run).content.decode())


def test_typed_values_are_not_overwritten(client, user, run):
    recorded_part(user, run, None, brand=MachineBrandFactory(name="Technogym"))
    client.force_login(user)

    assert prefill(client, run, level="7").status_code == 204
    replaced = prefill(client, run, level="7", machine_prefilled="1")
    assert 'value="Technogym"' in fields_of(replaced.content.decode())


def test_new_form_from_chooser_is_prefilled(client, user, run, gym):
    gym.is_default = True
    gym.save(update_fields=["is_default"])
    recorded_part(user, run, gym, brand=MachineBrandFactory(name="Technogym"), level=6)
    client.force_login(user)

    content = client.get(reverse("cardio_create"), {"sport": run.pk}).content.decode()

    assert 'value="Technogym"' in fields_of(content)
    assert "Как в прошлый раз" in content


def test_model_hints_follow_brand_and_isolation(client, user, other_user):
    brand = MachineBrandFactory(name="Technogym")
    MachineModelFactory(brand=brand, name="Run 1000")
    MachineModelFactory(brand=brand, name="Чужая модель", owner=other_user)
    client.force_login(user)

    content = client.get(
        reverse("cardio_machine_models"), {"machine_brand": "technogym"}
    ).content.decode()

    assert 'value="Run 1000"' in content
    assert "Чужая модель" not in content


# ---------- Справочник и корзина ----------


def test_brand_used_in_cardio_is_not_deleted_and_counted(client, user, run):
    brand = MachineBrandFactory(name="Kettler", owner=user)
    recorded_part(user, run, None, brand=brand)
    client.force_login(user)

    page = client.get(reverse("my_machines")).content.decode()
    assert "в 1 кардио" in page

    client.post(reverse("machine_delete", args=["brand", brand.pk]))

    assert MachineBrand.objects.filter(pk=brand.pk).exists()


def test_restore_brings_machine_back_even_if_brand_was_deleted(client, user, run):
    brand = MachineBrandFactory(name="Kettler", owner=user)
    model = MachineModelFactory(brand=brand, name="Axos", owner=user)
    part = recorded_part(user, run, None, brand=brand, model=model, level=4)
    client.force_login(user)
    client.post(reverse("workout_delete", args=[part.workout.pk]))
    # Тренировка в корзине больше не держит производителя — его можно удалить.
    client.post(reverse("machine_delete", args=["brand", brand.pk]))
    assert not MachineBrand.objects.exists()

    client.post(reverse("workout_restore", args=[DeletedWorkout.objects.get().pk]))

    part = CardioPart.objects.get()
    assert part.machine == "Kettler · Axos"
    assert part.brand.owner == user
    assert part.level == 4
