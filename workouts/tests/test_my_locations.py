"""Экран «Мои места»: добавление на ходу, место по умолчанию, переименование."""

import pytest
from django.urls import reverse

from workouts.models import Location
from workouts.services import location_for_name
from workouts.tests.factories import LocationFactory, SportFactory, WorkoutFactory

pytestmark = pytest.mark.django_db


# ---------- Создание на ходу ----------


def test_location_is_created_for_current_user(client, user):
    client.force_login(user)
    client.post(reverse("my_locations"), {"name": "СпортЛайф"})

    place = Location.objects.get(name="СпортЛайф")
    assert place.owner == user


@pytest.mark.parametrize("typed", ["СпортЛайф", "спортлайф", "СПОРТЛАЙФ"])
def test_existing_name_returns_the_same_location(user, typed):
    """Совпадение имени — «это оно», а не ошибка дубля: контракт быстрого создания."""
    place = LocationFactory(owner=user, name="СпортЛайф")

    assert location_for_name(user, typed) == place
    assert Location.objects.filter(owner=user).count() == 1


def test_existing_name_keeps_its_spelling(user):
    """Написание не канонизируется в текст — возвращается сама запись."""
    LocationFactory(owner=user, name="СпортЛайф")

    assert location_for_name(user, "спортлайф").name == "СпортЛайф"


def test_spelling_is_normalized(client, user):
    """Лишние пробелы схлопываются: иначе iexact не нашёл бы запись и упал на индексе."""
    LocationFactory(owner=user, name="Спорт лайф")

    client.force_login(user)
    client.post(reverse("my_locations"), {"name": "  Спорт   лайф "})

    assert Location.objects.filter(owner=user).count() == 1


def test_other_users_name_is_not_reused(user, other_user):
    """Одноимённое место чужого пользователя — не моё: создаётся своя запись."""
    LocationFactory(owner=other_user, name="СпортЛайф")

    mine = location_for_name(user, "СпортЛайф")

    assert mine.owner == user
    assert Location.objects.filter(name="СпортЛайф").count() == 2


def test_empty_name_creates_nothing(client, user):
    client.force_login(user)
    response = client.post(reverse("my_locations"), {"name": "   "})

    assert not Location.objects.exists()
    assert "Введите название." in response.content.decode()


def test_first_location_becomes_default(user):
    """Иначе молчаливая подстановка молчала бы, пока не заглянешь в профиль."""
    place = location_for_name(user, "СпортЛайф")

    assert place.is_default


def test_second_location_does_not_steal_the_default(user):
    first = location_for_name(user, "СпортЛайф")
    second = location_for_name(user, "Дома")

    first.refresh_from_db()
    assert first.is_default
    assert not second.is_default


def test_adding_existing_name_says_it_is_already_there(client, user):
    LocationFactory(owner=user, name="СпортЛайф")

    client.force_login(user)
    response = client.post(reverse("my_locations"), {"name": "спортлайф"}, follow=True)

    assert "уже есть" in response.content.decode()
    assert Location.objects.filter(owner=user).count() == 1


# ---------- Место по умолчанию ----------


def test_choosing_default_clears_the_previous_one(client, user):
    old = LocationFactory(owner=user, name="СпортЛайф", is_default=True)
    new = LocationFactory(owner=user, name="Дома")

    client.force_login(user)
    client.post(reverse("location_default", args=[new.pk]))

    old.refresh_from_db()
    new.refresh_from_db()
    assert not old.is_default
    assert new.is_default


def test_tapping_the_active_default_clears_it(client, user):
    place = LocationFactory(owner=user, name="СпортЛайф", is_default=True)

    client.force_login(user)
    client.post(reverse("location_default", args=[place.pk]))

    place.refresh_from_db()
    assert not place.is_default


def test_default_of_another_user_is_untouched(client, user, other_user):
    theirs = LocationFactory(owner=other_user, name="Чужой зал", is_default=True)
    mine = LocationFactory(owner=user, name="СпортЛайф")

    client.force_login(user)
    client.post(reverse("location_default", args=[mine.pk]))

    theirs.refresh_from_db()
    assert theirs.is_default


def test_deleting_the_default_leaves_no_default(client, user):
    place = LocationFactory(owner=user, name="СпортЛайф", is_default=True)

    client.force_login(user)
    client.post(reverse("location_delete", args=[place.pk]))

    assert Location.objects.default_for(user) is None


# ---------- Переименование ----------


def test_rename_changes_the_name_in_history(client, user):
    """Ради этого место и стало моделью: опечатка правится один раз."""
    place = LocationFactory(owner=user, name="Спортлйф")
    workout = WorkoutFactory(user=user, location=place)

    client.force_login(user)
    client.post(reverse("location_rename", args=[place.pk]), {"name": "СпортЛайф"})

    workout.refresh_from_db()
    assert workout.location.name == "СпортЛайф"
    assert Location.objects.filter(owner=user).count() == 1


@pytest.mark.parametrize("typed", ["Дома", "дома"])
def test_rename_to_an_existing_name_is_rejected(client, user, typed):
    LocationFactory(owner=user, name="Дома")
    place = LocationFactory(owner=user, name="СпортЛайф")

    client.force_login(user)
    response = client.post(reverse("location_rename", args=[place.pk]), {"name": typed})

    place.refresh_from_db()
    assert place.name == "СпортЛайф"
    assert "уже есть" in response.content.decode()


def test_rename_to_the_same_name_is_allowed(client, user):
    """Собственное имя не считается занятым — иначе правка регистра была бы невозможна."""
    place = LocationFactory(owner=user, name="спортлайф")

    client.force_login(user)
    client.post(reverse("location_rename", args=[place.pk]), {"name": "СпортЛайф"})

    place.refresh_from_db()
    assert place.name == "СпортЛайф"


def test_rename_keeps_the_default_flag(client, user):
    place = LocationFactory(owner=user, name="Спортлйф", is_default=True)

    client.force_login(user)
    client.post(reverse("location_rename", args=[place.pk]), {"name": "СпортЛайф"})

    place.refresh_from_db()
    assert place.is_default


def test_rename_closes_the_modal_out_of_band(client, user):
    """Список приходит OOB, поэтому в #modal попадает пустой остаток ответа."""
    place = LocationFactory(owner=user, name="Спортлйф")

    client.force_login(user)
    content = client.post(
        reverse("location_rename", args=[place.pk]), {"name": "СпортЛайф"}
    ).content.decode()

    assert 'hx-swap-oob="true"' in content
    assert 'id="location-rows"' in content
    assert "Переименовать место" not in content


# ---------- Экран ----------


def test_my_locations_lists_only_own_records(client, user, other_user):
    mine = LocationFactory(owner=user, name="СпортЛайф")
    LocationFactory(owner=other_user, name="Чужой зал")

    client.force_login(user)
    response = client.get(reverse("my_locations"))

    content = response.content.decode()
    assert list(response.context["locations"]) == [mine]
    assert "СпортЛайф" in content
    assert "Чужой зал" not in content


def test_my_locations_shows_usage_label(client, user):
    place = LocationFactory(owner=user, name="СпортЛайф")
    WorkoutFactory(user=user, location=place)

    client.force_login(user)
    content = client.get(reverse("my_locations")).content.decode()

    assert "в 1 тренировке" in content


def test_my_locations_counters_ignore_drafts(client, user):
    """Черновик держит место, но записанной тренировкой не стал."""
    place = LocationFactory(owner=user, name="СпортЛайф")
    WorkoutFactory(user=user, location=place, started_at=None, duration_min=None)

    client.force_login(user)
    content = client.get(reverse("my_locations")).content.decode()

    assert "не использовалось" in content


def test_my_locations_counters_ignore_other_users_workouts(client, user, other_user):
    """Изоляция: чужая тренировка не делает моё место использованным."""
    place = LocationFactory(owner=user, name="СпортЛайф")
    WorkoutFactory(user=other_user, location=place)

    client.force_login(user)
    content = client.get(reverse("my_locations")).content.decode()

    assert "не использовалось" in content


def test_my_locations_shows_empty_state(client, user):
    client.force_login(user)
    content = client.get(reverse("my_locations")).content.decode()

    assert "Мест пока нет" in content


def test_default_location_is_listed_first(client, user):
    """Аннотация добавляет GROUP BY, а с ним Django игнорирует Meta.ordering."""
    LocationFactory(owner=user, name="Азов")
    default = LocationFactory(owner=user, name="Ялта", is_default=True)

    client.force_login(user)
    response = client.get(reverse("my_locations"))

    assert [row.pk for row in response.context["locations"]][0] == default.pk


def test_default_row_is_marked(client, user):
    LocationFactory(owner=user, name="СпортЛайф", is_default=True)

    client.force_login(user)
    content = client.get(reverse("my_locations")).content.decode()

    assert "по умолчанию" in content


# ---------- Удаление ----------


def test_unused_location_is_deleted(client, user):
    place = LocationFactory(owner=user, name="СпортЛайф")

    client.force_login(user)
    client.post(reverse("location_delete", args=[place.pk]))

    assert not Location.objects.filter(pk=place.pk).exists()


def test_used_location_is_kept_with_message(client, user):
    place = LocationFactory(owner=user, name="СпортЛайф")
    WorkoutFactory(user=user, location=place)

    client.force_login(user)
    page = client.get(reverse("location_delete", args=[place.pk])).content.decode()
    client.post(reverse("location_delete", args=[place.pk]))

    assert "нельзя удалить" in page
    assert "Удалить</button>" not in page
    assert Location.objects.filter(pk=place.pk).exists()


def test_draft_blocks_location_deletion_with_honest_message(client, user):
    """У черновика подпись «не использовалось», но FK он держит — сообщение другое."""
    place = LocationFactory(owner=user, name="СпортЛайф")
    sport = SportFactory(owner=user, name="Кроссфит")
    WorkoutFactory(user=user, sport=sport, location=place, started_at=None, duration_min=None)

    client.force_login(user)
    page = client.get(reverse("location_delete", args=[place.pk])).content.decode()

    assert "сначала удалите черновик" in page
    assert "Удалить</button>" not in page


def test_get_does_not_delete_location(client, user):
    place = LocationFactory(owner=user, name="СпортЛайф")

    client.force_login(user)
    client.get(reverse("location_delete", args=[place.pk]))

    assert Location.objects.filter(pk=place.pk).exists()


# ---------- Изоляция ----------

FOREIGN_ROUTES = [
    pytest.param("location_default", "post", id="default"),
    pytest.param("location_rename", "get", id="rename-modal"),
    pytest.param("location_rename", "post", id="rename"),
    pytest.param("location_delete", "get", id="delete-page"),
    pytest.param("location_delete", "post", id="delete"),
]


@pytest.mark.parametrize(("url_name", "method"), FOREIGN_ROUTES)
def test_foreign_location_routes_are_404(client, user, other_user, url_name, method):
    theirs = LocationFactory(owner=other_user, name="Чужой зал", is_default=True)

    client.force_login(user)
    response = getattr(client, method)(reverse(url_name, args=[theirs.pk]), {"name": "Взлом"})

    assert response.status_code == 404
    theirs.refresh_from_db()
    assert theirs.name == "Чужой зал"
    assert theirs.is_default


@pytest.mark.parametrize(("url_name", "method"), FOREIGN_ROUTES)
def test_anonymous_cannot_manage_locations(client, user, url_name, method):
    place = LocationFactory(owner=user, name="СпортЛайф")

    response = getattr(client, method)(reverse(url_name, args=[place.pk]), {"name": "Взлом"})

    assert response.status_code == 302
    place.refresh_from_db()
    assert place.name == "СпортЛайф"


def test_anonymous_cannot_see_my_locations(client):
    response = client.get(reverse("my_locations"))

    assert response.status_code == 302
