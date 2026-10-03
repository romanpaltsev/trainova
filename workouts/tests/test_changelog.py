"""Страница «Что нового» и логика прочитанного."""

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
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


def test_fix_keeps_its_kind_badge_when_read(client, user):
    """«Исправлено» — тип новости, а не «непрочитано»: он остаётся всегда."""
    fix = ChangelogEntryFactory(kind=ChangelogEntry.Kind.FIX)
    fix.read_by.add(user)

    client.force_login(user)
    content = client.get(reverse("changelog")).content.decode()

    assert "Исправлено" in content
    assert "app-badge-new" not in content


def test_read_feature_has_no_badge_at_all(client, user):
    """У прочитанной новости-функции ярлыков нет: «Новое» было меткой, а не типом."""
    entry = ChangelogEntryFactory(kind=ChangelogEntry.Kind.FEATURE)
    entry.read_by.add(user)

    client.force_login(user)
    content = client.get(reverse("changelog")).content.decode()

    assert "app-badge" not in content
    assert "hx-post" not in content.split('<div class="app-news-list">', 1)[1]


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


# ---------- Метка «Новое»: гаснет по нажатию на новость ----------


def card(content, entry):
    """Разметка одной карточки новости."""
    return content.split(f'id="news-{entry.pk}"', 1)[1].split("</article>", 1)[0]


def test_unread_news_shows_new_badge_and_read_action(client, user):
    entry = ChangelogEntryFactory(kind=ChangelogEntry.Kind.FIX)

    client.force_login(user)
    html = card(client.get(reverse("changelog")).content.decode(), entry)

    assert "app-badge-new" in html
    assert "Исправлено" in html  # тип стоит рядом с меткой
    assert f'hx-post="{reverse("changelog_read", args=[entry.pk])}"' in html


def test_news_published_before_joining_are_not_new(client, user):
    """Приглашённый друг не видит «Новое» на всей истории проекта."""
    ChangelogEntryFactory(published_at=user.date_joined - timedelta(days=1))

    client.force_login(user)
    content = client.get(reverse("changelog")).content.decode()

    assert "app-badge-new" not in content


def test_opening_page_does_not_read_news(client, user):
    """Открытие страницы гасит точку в профиле, но не метки карточек."""
    entry = ChangelogEntryFactory()

    client.force_login(user)
    client.get(reverse("changelog"))

    assert not entry.read_by.filter(pk=user.pk).exists()
    assert "app-badge-new" in client.get(reverse("changelog")).content.decode()


def test_marking_read_returns_card_without_badge(client, user):
    entry = ChangelogEntryFactory(title="Свежая новость")

    client.force_login(user)
    response = client.post(reverse("changelog_read", args=[entry.pk]))

    html = response.content.decode()
    assert response.status_code == 200
    assert "Свежая новость" in html
    assert "app-badge-new" not in html
    assert "hx-post" not in html
    assert entry.read_by.filter(pk=user.pk).exists()


def test_marking_read_twice_keeps_one_row(client, user):
    entry = ChangelogEntryFactory()

    client.force_login(user)
    client.post(reverse("changelog_read", args=[entry.pk]))
    client.post(reverse("changelog_read", args=[entry.pk]))

    assert entry.read_by.count() == 1


def test_marking_read_is_isolated_between_users(client, user, other_user):
    """Священное правило: прочитанное у меня не гасит «Новое» у другого."""
    entry = ChangelogEntryFactory()

    client.force_login(user)
    client.post(reverse("changelog_read", args=[entry.pk]))

    assert list(entry.read_by.all()) == [user]
    client.force_login(other_user)
    assert "app-badge-new" in client.get(reverse("changelog")).content.decode()


@pytest.mark.parametrize("case", ["unpublished", "future", "missing"])
def test_marking_read_hidden_entry_is_404(client, user, case):
    if case == "missing":
        pk = 999999
    else:
        entry = ChangelogEntryFactory(
            is_published=case != "unpublished",
            published_at=timezone.now() + timedelta(days=1 if case == "future" else 0),
        )
        pk = entry.pk

    client.force_login(user)

    assert client.post(reverse("changelog_read", args=[pk])).status_code == 404


def test_marking_read_requires_login_and_post(client, user):
    entry = ChangelogEntryFactory()

    response = client.post(reverse("changelog_read", args=[entry.pk]))
    assert response.status_code == 302
    assert reverse("account_login") in response.url

    client.force_login(user)
    assert client.get(reverse("changelog_read", args=[entry.pk])).status_code == 405


@pytest.mark.parametrize("count", [2, 12])
def test_changelog_page_queries_do_not_scale(client, user, count, django_assert_max_num_queries):
    """Пометка «прочитано» — подзапросом EXISTS в том же запросе: ни строки на
    карточку. Сессия, пользователь, новости, запись changelog_seen_at и пара
    SAVEPOINT/RELEASE транзакции запроса."""
    for _ in range(count):
        ChangelogEntryFactory().read_by.add(user)
    ChangelogEntryFactory()

    client.force_login(user)
    with django_assert_max_num_queries(6):
        client.get(reverse("changelog"))


def test_migration_marks_news_seen_before_last_visit(user, other_user):
    """После деплоя прочитанным считается всё, что человек уже видел на
    странице: до его последнего открытия «Что нового»."""
    migration = importlib.import_module("workouts.migrations.0045_mark_seen_changelog_read")
    now = timezone.now()
    seen = ChangelogEntryFactory(published_at=now - timedelta(days=3))
    fresh = ChangelogEntryFactory(published_at=now)
    draft = ChangelogEntryFactory(published_at=now - timedelta(days=3), is_published=False)
    user.changelog_seen_at = now - timedelta(days=1)
    user.save(update_fields=["changelog_seen_at"])

    migration.mark_seen_as_read(django_apps, None)

    assert list(user.read_changelog_entries.all()) == [seen]
    assert not fresh.read_by.exists()
    assert not draft.read_by.exists()
    # Кто страницу не открывал, тому прочитанного не выдумываем.
    assert not other_user.read_changelog_entries.exists()
