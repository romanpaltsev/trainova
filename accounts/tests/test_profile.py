"""Экран профиля: аккаунт, тема, отдых по умолчанию, входы в справочники."""

import pytest
from django.urls import reverse

from workouts.models import Workout
from workouts.tests.factories import (
    ChangelogEntryFactory,
    ExerciseFactory,
    SportFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def test_profile_requires_login(client):
    response = client.get(reverse("profile"))

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_profile_renders_email_with_verified_mark(client, user):
    client.force_login(user)

    content = client.get(reverse("profile")).content.decode()

    assert user.email in content
    assert "email подтверждён" in content


def test_profile_shows_unverified_email_without_mark(client, other_user):
    """У other_user нет подтверждённого адреса — отметки быть не должно."""
    client.force_login(other_user)

    content = client.get(reverse("profile")).content.decode()

    assert "email не подтверждён" in content


def test_profile_shows_theme_choice_with_three_options(client, user):
    client.force_login(user)

    content = client.get(reverse("profile")).content.decode()

    assert "data-app-theme-choice" in content
    for value in ("light", "dark", "system"):
        assert f'value="{value}"' in content


def test_profile_shows_current_rest_default(client, user):
    client.force_login(user)

    content = client.get(reverse("profile")).content.decode()

    assert "90 сек" in content


def test_profile_shows_counts_of_personal_catalogs(client, user):
    ExerciseFactory(owner=user)
    ExerciseFactory(owner=user)
    SportFactory(owner=user)

    client.force_login(user)
    response = client.get(reverse("profile"))

    assert response.context["exercises_count"] == 2
    assert response.context["sports_count"] == 1


def test_profile_counts_ignore_global_and_other_users_records(client, user, other_user):
    ExerciseFactory()  # глобальное
    ExerciseFactory(owner=other_user)
    SportFactory(owner=other_user)

    client.force_login(user)
    response = client.get(reverse("profile"))

    assert response.context["exercises_count"] == 0
    assert response.context["sports_count"] == 0


def test_profile_links_to_password_change_and_logout(client, user):
    client.force_login(user)

    content = client.get(reverse("profile")).content.decode()

    assert reverse("account_change_password") in content
    assert reverse("account_logout") in content


def test_profile_shows_app_version(client, user):
    client.force_login(user)

    response = client.get(reverse("profile"))

    assert response.context["app_version"]
    assert response.context["app_version"] in response.content.decode()


def test_profile_shows_dot_when_unread_entries_exist(client, user):
    ChangelogEntryFactory()

    client.force_login(user)
    response = client.get(reverse("profile"))

    assert response.context["changelog_unread"] is True
    assert "есть новые записи" in response.content.decode()


def test_profile_hides_dot_when_everything_is_read(client, user):
    ChangelogEntryFactory()

    client.force_login(user)
    client.get(reverse("changelog"))
    response = client.get(reverse("profile"))

    assert response.context["changelog_unread"] is False
    assert "есть новые записи" not in response.content.decode()


def test_rest_modal_requires_login(client):
    response = client.get(reverse("profile_rest"))

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_rest_modal_renders_current_value(client, user):
    client.force_login(user)

    response = client.get(reverse("profile_rest"))

    content = response.content.decode()
    assert response.status_code == 200
    assert "accounts/_rest_modal.html" in [t.name for t in response.templates]
    assert "1:30" in content


@pytest.mark.parametrize(
    ("delta", "expected"),
    [pytest.param("-15", 75, id="minus"), pytest.param("15", 105, id="plus")],
)
def test_rest_step_changes_default(client, user, delta, expected):
    client.force_login(user)

    client.post(reverse("profile_rest"), {"delta": delta})

    user.refresh_from_db()
    assert user.rest_seconds_default == expected


@pytest.mark.parametrize(
    ("start", "delta", "expected"),
    [pytest.param(15, "-15", 15, id="minimum"), pytest.param(600, "15", 600, id="maximum")],
)
def test_rest_step_is_clamped(client, user, start, delta, expected):
    user.rest_seconds_default = start
    user.save(update_fields=["rest_seconds_default"])

    client.force_login(user)
    client.post(reverse("profile_rest"), {"delta": delta})

    user.refresh_from_db()
    assert user.rest_seconds_default == expected


def test_rest_step_rejects_unknown_delta(client, user):
    client.force_login(user)

    response = client.post(reverse("profile_rest"), {"delta": "600"})

    user.refresh_from_db()
    assert response.status_code == 400
    assert user.rest_seconds_default == 90


def test_rest_step_updates_profile_row_out_of_band(client, user):
    client.force_login(user)

    content = client.post(reverse("profile_rest"), {"delta": "15"}).content.decode()

    assert 'hx-swap-oob="true"' in content
    assert 'id="profile-rest"' in content
    assert "105 сек" in content


def test_rest_step_does_not_touch_other_users_default(client, user, other_user):
    client.force_login(user)

    client.post(reverse("profile_rest"), {"delta": "15"})

    other_user.refresh_from_db()
    assert other_user.rest_seconds_default == 90


def test_new_default_applies_to_next_workout_timer(client, user):
    """Настройка профиля — это и есть отдых таймера живого режима."""
    client.force_login(user)
    client.post(reverse("profile_rest"), {"delta": "15"})

    workout = WorkoutFactory(user=user, duration_min=None, rest_seconds=None)

    assert Workout.objects.get(pk=workout.pk).effective_rest_seconds == 105
