"""«Админка»: раздел виден и открывается только администратору.

Главное здесь — доступ: обычный пользователь не должен ни увидеть вход в раздел,
ни открыть его по прямому адресу (404, как чужая запись). Остальное — что
страницы честно считают то, что показывают.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import UserFactory
from workouts.models import DeletedWorkout
from workouts.tests.factories import WorkoutFactory

pytestmark = pytest.mark.django_db

PAGES = ["admin_home", "admin_users", "admin_system"]


@pytest.fixture
def admin_user(user):
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    return user


@pytest.mark.parametrize("url_name", PAGES)
def test_guest_is_sent_to_login(client, url_name):
    response = client.get(reverse(url_name))

    assert response.status_code == 302
    assert response.url.startswith(reverse("account_login"))


@pytest.mark.parametrize("url_name", PAGES)
def test_regular_user_gets_404(client, user, url_name):
    """Не 403: обычному пользователю незачем знать, что раздел существует."""
    client.force_login(user)

    assert client.get(reverse(url_name)).status_code == 404


@pytest.mark.parametrize("url_name", PAGES)
def test_staff_opens_every_page(client, admin_user, url_name):
    client.force_login(admin_user)

    assert client.get(reverse(url_name)).status_code == 200


def test_superuser_without_staff_flag_is_admin_too(client, user):
    """Флаг is_staff у суперпользователя можно снять в Django admin — админом он остаётся."""
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    client.force_login(user)

    assert client.get(reverse("admin_home")).status_code == 200


def test_admin_section_lives_at_admin_and_django_admin_moved(client, admin_user):
    client.force_login(admin_user)

    assert reverse("admin_home") == "/admin/"
    assert reverse("admin:index") == "/django-admin/"
    assert client.get("/django-admin/").status_code == 200


def test_entry_points_are_shown_only_to_admin(client, user):
    """Пункт боковой панели и строка профиля — только администратору."""
    entry = f'href="{reverse("admin_home")}"'
    client.force_login(user)
    assert entry not in client.get(reverse("profile")).content.decode()

    user.is_staff = True
    user.save(update_fields=["is_staff"])
    html = client.get(reverse("profile")).content.decode()

    assert html.count(entry) == 2  # панель и строка профиля
    assert 'title="Админка"' in html


def test_users_page_lists_everyone_with_their_workouts(client, admin_user):
    friend = UserFactory(email="friend@example.com")
    WorkoutFactory(user=friend)
    WorkoutFactory(user=friend, duration_min=None)  # идёт — не записана, не считается
    client.force_login(admin_user)

    users = {u.email: u for u in client.get(reverse("admin_users")).context["users"]}

    assert set(users) == {admin_user.email, "friend@example.com"}
    assert users["friend@example.com"].workouts_count == 1
    assert users["friend@example.com"].email_verified is False
    assert users[admin_user.email].status == "Администратор"


def test_users_page_shows_pending_deletion(client, admin_user):
    leaving = UserFactory(is_active=False, deletion_requested_at=timezone.now() - timedelta(days=1))
    client.force_login(admin_user)

    users = {u.pk: u for u in client.get(reverse("admin_users")).context["users"]}

    assert users[leaving.pk].status.startswith("Ждёт удаления до ")


def test_users_page_queries_do_not_grow_with_users(client, admin_user):
    """Тренировки и подтверждение почты — аннотациями одного запроса, а не по запросу на строку."""
    client.force_login(admin_user)

    def count_queries():
        with CaptureQueriesContext(connection) as queries:
            client.get(reverse("admin_users"))
        return len(queries)

    UserFactory.create_batch(2)
    few = count_queries()
    for person in UserFactory.create_batch(8):
        WorkoutFactory(user=person)

    assert count_queries() == few


def test_system_page_counts_what_nightly_purge_will_delete(client, admin_user, other_user):
    now = timezone.now()
    DeletedWorkout.objects.create(
        user=other_user, deleted_at=now - timedelta(days=31), title="старая", payload={}
    )
    DeletedWorkout.objects.create(user=other_user, deleted_at=now, title="свежая", payload={})
    UserFactory(is_active=False, deletion_requested_at=now - timedelta(days=31))
    UserFactory(is_active=False, deletion_requested_at=now)
    client.force_login(admin_user)

    context = client.get(reverse("admin_system")).context

    assert context["expired_trash"] == 1
    assert context["expired_accounts"] == 1
    assert context["pending_migrations"] == []
    assert dict(context["counts"])["В корзине"] == 2
