"""«Обратная связь»: пользователь пишет, администратор читает и отвечает.

Изоляция: свои обращения видит только автор, чужие — только администратор в
«Админке». Письма: админам — о новом обращении, автору — об ответе.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.deletion import delete_user_data
from accounts.tests.factories import UserFactory
from feedback.forms import DAILY_LIMIT
from feedback.models import Feedback
from workouts.tests.budgets import SIDEBAR_QUERIES

pytestmark = pytest.mark.django_db

URL = reverse("feedback")


@pytest.fixture
def admin_user(db):
    return UserFactory(email="boss@example.com", is_staff=True)


def send(client, text="Добавьте тёмную тему для графиков", kind="idea"):
    return client.post(URL, {"kind": kind, "text": text})


def test_guest_is_sent_to_login(client):
    response = client.get(URL)

    assert response.status_code == 302
    assert response.url.startswith(reverse("account_login"))


def test_user_sends_feedback_and_admins_get_mail(client, user, admin_user):
    client.force_login(user)

    response = send(client, kind="bug", text="  Кнопка не нажимается  ")

    assert response.status_code == 302
    item = Feedback.objects.get()
    assert (item.user, item.kind, item.text, item.status) == (
        user,
        "bug",
        "Кнопка не нажимается",
        "new",
    )
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["boss@example.com"]
    assert "Кнопка не нажимается" in mail.outbox[0].body
    assert reverse("admin_feedback_detail", args=[item.pk]) in mail.outbox[0].body


def test_empty_text_is_an_error(client, user):
    client.force_login(user)

    response = send(client, text="   ")

    assert response.status_code == 200
    assert "Напишите, что хотели сказать." in response.content.decode()
    assert not Feedback.objects.exists()


def test_daily_limit(client, user):
    Feedback.objects.bulk_create(Feedback(user=user, text=f"№{n}") for n in range(DAILY_LIMIT))
    client.force_login(user)

    response = send(client)

    assert "не больше 10 обращений" in response.content.decode()
    assert Feedback.objects.count() == DAILY_LIMIT


def test_old_feedback_does_not_count_toward_limit(client, user):
    Feedback.objects.bulk_create(Feedback(user=user, text=f"№{n}") for n in range(DAILY_LIMIT))
    Feedback.objects.update(created_at=timezone.now() - timedelta(days=2))
    client.force_login(user)

    send(client)

    assert Feedback.objects.count() == DAILY_LIMIT + 1


def test_user_sees_only_own_feedback(client, user, other_user):
    Feedback.objects.create(user=user, text="Моё обращение")
    Feedback.objects.create(user=other_user, text="Чужое обращение")
    client.force_login(user)

    html = client.get(URL).content.decode()

    assert "Моё обращение" in html
    assert "Чужое обращение" not in html


def test_admin_pages_are_404_for_regular_user(client, user):
    item = Feedback.objects.create(user=user, text="Вопрос")
    client.force_login(user)

    assert client.get(reverse("admin_feedback")).status_code == 404
    assert client.get(reverse("admin_feedback_detail", args=[item.pk])).status_code == 404
    response = client.post(
        reverse("admin_feedback_detail", args=[item.pk]), {"status": "done", "reply": "x"}
    )
    assert response.status_code == 404
    item.refresh_from_db()
    assert item.reply == ""


def test_admin_reply_is_shown_to_author_and_mailed(client, user, admin_user):
    item = Feedback.objects.create(user=user, text="Как удалить тренировку?")
    client.force_login(admin_user)

    client.post(
        reverse("admin_feedback_detail", args=[item.pk]),
        {"status": "done", "reply": "Кнопка «Удалить» в итоге тренировки."},
    )

    item.refresh_from_db()
    assert item.status == "done"
    assert item.replied_at is not None
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert "Кнопка «Удалить»" in mail.outbox[0].body
    client.force_login(user)
    assert "Кнопка «Удалить» в итоге тренировки." in client.get(URL).content.decode()


def test_status_change_alone_sends_no_mail(client, user, admin_user):
    item = Feedback.objects.create(user=user, text="Идея", reply="Спасибо!")
    client.force_login(admin_user)

    client.post(
        reverse("admin_feedback_detail", args=[item.pk]),
        {"status": "in_progress", "reply": "Спасибо!"},
    )

    item.refresh_from_db()
    assert item.status == "in_progress"
    assert mail.outbox == []


def test_admin_list_filters_by_status(client, user, admin_user):
    Feedback.objects.create(user=user, text="Новое дело")
    Feedback.objects.create(user=user, text="Готовое дело", status="done")
    client.force_login(admin_user)

    items = client.get(reverse("admin_feedback"), {"status": "done"}).context["items"]

    assert [item.text for item in items] == ["Готовое дело"]


def test_entry_points_on_profile_and_sidebar(client, user):
    client.force_login(user)

    html = client.get(reverse("profile")).content.decode()

    assert html.count(f'href="{URL}"') == 2  # панель и строка профиля
    assert 'title="Обратная связь"' in html


def test_feedback_page_queries_do_not_grow(client, user, django_assert_max_num_queries):
    """5: BEGIN и COMMIT транзакции запроса, сессия, пользователь, список."""
    Feedback.objects.bulk_create(Feedback(user=user, text=f"№{n}") for n in range(8))
    client.force_login(user)

    with django_assert_max_num_queries(5 + SIDEBAR_QUERIES):
        client.get(URL)


def test_deleting_user_deletes_their_feedback(user):
    Feedback.objects.create(user=user, text="Пока")

    delete_user_data(user)

    assert not Feedback.objects.exists()
