"""Страница «Что нового» и логика прочитанного."""

from datetime import timedelta

import pytest
from django.contrib import admin
from django.urls import reverse
from django.utils import timezone

from workouts.models import ChangelogEntry
from workouts.tests.factories import ChangelogEntryFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def without_shipped_announcements():
    """Пустая таблица новостей: тесты описывают логику на своих записях.

    Анонсы релизов приезжают data-миграциями, поэтому в тестовой базе они уже
    лежат — а «непрочитанных нет» и «список пуст» иначе не проверить.
    """
    ChangelogEntry.objects.all().delete()


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param("never-seen", True, id="never-seen"),
        pytest.param("newer", True, id="newer"),
        pytest.param("older", False, id="older"),
        pytest.param("unpublished", False, id="unpublished"),
        pytest.param("future", False, id="future"),
    ],
)
def test_unread_detection(user, case, expected):
    now = timezone.now()
    if case != "never-seen":
        user.changelog_seen_at = now - timedelta(days=1)
        user.save(update_fields=["changelog_seen_at"])
    published_at = now
    if case == "older":
        published_at = now - timedelta(days=2)
    elif case == "future":
        published_at = now + timedelta(days=2)
    ChangelogEntryFactory(published_at=published_at, is_published=case != "unpublished")

    assert ChangelogEntry.objects.unread_for(user).exists() is expected


def test_changelog_requires_login(client):
    response = client.get(reverse("changelog"))

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_changelog_lists_published_entries_newest_first(client, user):
    now = timezone.now()
    old = ChangelogEntryFactory(title="Старая новость", published_at=now - timedelta(days=5))
    fresh = ChangelogEntryFactory(title="Свежая новость", published_at=now)

    client.force_login(user)
    entries = list(client.get(reverse("changelog")).context["entries"])

    assert entries == [fresh, old]


def test_changelog_hides_unpublished_and_future_entries(client, user):
    draft = ChangelogEntryFactory(title="Черновик", is_published=False)
    future = ChangelogEntryFactory(
        title="Из будущего", published_at=timezone.now() + timedelta(days=1)
    )
    visible = ChangelogEntryFactory(title="Видимая")

    client.force_login(user)
    content = client.get(reverse("changelog")).content.decode()

    assert visible.title in content
    assert draft.title not in content
    assert future.title not in content


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        pytest.param(ChangelogEntry.Kind.FEATURE, "Новое", id="feature"),
        pytest.param(ChangelogEntry.Kind.FIX, "Исправлено", id="fix"),
    ],
)
def test_changelog_renders_kind_badge(client, user, kind, label):
    ChangelogEntryFactory(kind=kind)

    client.force_login(user)
    content = client.get(reverse("changelog")).content.decode()

    assert label in content
    assert f"app-badge-{kind.value}" in content


def test_changelog_renders_russian_date(client, user):
    ChangelogEntryFactory(published_at=timezone.make_aware(timezone.datetime(2026, 8, 25, 12)))

    client.force_login(user)
    content = client.get(reverse("changelog")).content.decode()

    assert "25 августа" in content


def test_changelog_shows_empty_state(client, user):
    client.force_login(user)

    content = client.get(reverse("changelog")).content.decode()

    assert "Пока никаких новостей" in content


def test_changelog_rejects_post(client, user):
    """Записи создаёт только админ — у страницы нет POST."""
    client.force_login(user)

    assert client.post(reverse("changelog")).status_code == 405


def test_changelog_entry_is_registered_in_admin():
    assert admin.site.is_registered(ChangelogEntry)


def test_opening_changelog_marks_it_seen(client, user):
    ChangelogEntryFactory()
    assert user.changelog_seen_at is None

    client.force_login(user)
    client.get(reverse("changelog"))

    user.refresh_from_db()
    assert user.changelog_seen_at is not None


def test_opening_changelog_again_moves_seen_at_forward(client, user):
    client.force_login(user)
    client.get(reverse("changelog"))
    user.refresh_from_db()
    first = user.changelog_seen_at

    client.get(reverse("changelog"))

    user.refresh_from_db()
    assert user.changelog_seen_at > first


def test_seen_at_is_isolated_between_users(client, user, other_user):
    client.force_login(user)
    client.get(reverse("changelog"))

    other_user.refresh_from_db()
    assert other_user.changelog_seen_at is None
