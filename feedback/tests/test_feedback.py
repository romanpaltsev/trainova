"""«Обратная связь»: пользователь пишет, администратор читает и отвечает.

Изоляция: свои обращения видит только автор, чужие — только администратор в
«Админке». Письма: админам — о новом обращении и дописке, автору — об ответе.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.deletion import delete_user_data
from accounts.tests.factories import UserFactory
from feedback.forms import DAILY_LIMIT
from feedback.models import Feedback, FeedbackMessage
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

    assert "не больше 10 сообщений" in response.content.decode()
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


def detail(item):
    return reverse("feedback_detail", args=[item.pk])


def admin_detail(item):
    return reverse("admin_feedback_detail", args=[item.pk])


def test_admin_pages_are_404_for_regular_user(client, user):
    item = Feedback.objects.create(user=user, text="Вопрос")
    client.force_login(user)

    assert client.get(reverse("admin_feedback")).status_code == 404
    assert client.get(admin_detail(item)).status_code == 404
    response = client.post(admin_detail(item), {"status": "done", "text": "x"})
    assert response.status_code == 404
    item.refresh_from_db()
    assert item.status == "new"
    assert not FeedbackMessage.objects.exists()


def test_someone_elses_feedback_is_404(client, user, other_user):
    theirs = Feedback.objects.create(user=other_user, text="Чужое")
    client.force_login(user)

    assert client.get(detail(theirs)).status_code == 404
    assert client.post(detail(theirs), {"text": "Влезть в чужое"}).status_code == 404
    assert not FeedbackMessage.objects.exists()


def test_admin_reply_is_shown_to_author_and_mailed(client, user, admin_user):
    item = Feedback.objects.create(user=user, text="Как удалить тренировку?")
    client.force_login(admin_user)

    client.post(admin_detail(item), {"status": "done", "text": "Кнопка «Удалить» в итоге."})

    item.refresh_from_db()
    assert item.status == "done"
    message = item.messages.get()
    assert message.from_admin
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert "Кнопка «Удалить»" in mail.outbox[0].body
    assert detail(item) in mail.outbox[0].body
    client.force_login(user)
    assert "Кнопка «Удалить» в итоге." in client.get(detail(item)).content.decode()


def test_status_change_alone_sends_no_mail(client, user, admin_user):
    item = Feedback.objects.create(user=user, text="Идея")
    client.force_login(admin_user)

    client.post(admin_detail(item), {"status": "in_progress", "text": "  "})

    item.refresh_from_db()
    assert item.status == "in_progress"
    assert not item.messages.exists()
    assert mail.outbox == []


def test_author_follow_up_reopens_and_mails_admins(client, user, admin_user):
    item = Feedback.objects.create(user=user, text="Идея", status="done")
    FeedbackMessage.objects.create(feedback=item, from_admin=True, text="Сделано")
    client.force_login(user)

    response = client.post(detail(item), {"text": "А можно ещё и на ПК?"})

    assert response.status_code == 302
    item.refresh_from_db()
    assert item.status == "new"
    last = item.messages.last()
    assert (last.text, last.from_admin) == ("А можно ещё и на ПК?", False)
    assert mail.outbox[0].to == ["boss@example.com"]
    assert "А можно ещё и на ПК?" in mail.outbox[0].body
    assert admin_detail(item) in mail.outbox[0].body


def test_empty_follow_up_is_an_error(client, user):
    item = Feedback.objects.create(user=user, text="Идея")
    client.force_login(user)

    response = client.post(detail(item), {"text": " "})

    assert "Напишите, что хотели сказать." in response.content.decode()
    assert not item.messages.exists()


def test_daily_limit_counts_follow_ups(client, user):
    item = Feedback.objects.create(user=user, text="Первое")
    FeedbackMessage.objects.bulk_create(
        FeedbackMessage(feedback=item, text=f"№{n}") for n in range(DAILY_LIMIT - 1)
    )
    client.force_login(user)

    send(client)
    client.post(detail(item), {"text": "Ещё"})

    assert Feedback.objects.count() == 1
    assert item.messages.count() == DAILY_LIMIT - 1


def test_admin_replies_are_not_limited(client, user, admin_user):
    item = Feedback.objects.create(user=user, text="Вопрос")
    FeedbackMessage.objects.bulk_create(
        FeedbackMessage(feedback=item, from_admin=True, text=f"№{n}") for n in range(DAILY_LIMIT)
    )
    client.force_login(admin_user)

    client.post(admin_detail(item), {"status": "new", "text": "Ещё ответ"})

    assert item.messages.count() == DAILY_LIMIT + 1


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
    items = Feedback.objects.bulk_create(Feedback(user=user, text=f"№{n}") for n in range(8))
    FeedbackMessage.objects.bulk_create(
        FeedbackMessage(feedback=item, from_admin=True, text="Ответ") for item in items
    )
    client.force_login(user)

    with django_assert_max_num_queries(5 + SIDEBAR_QUERIES):
        client.get(URL)


def test_thread_page_queries_do_not_grow(client, user, django_assert_max_num_queries):
    """7: BEGIN и COMMIT, сессия, пользователь, обращение, отметка «прочитано», сообщения."""
    item = Feedback.objects.create(user=user, text="Вопрос")
    FeedbackMessage.objects.bulk_create(
        FeedbackMessage(feedback=item, from_admin=n % 2 == 0, text=f"№{n}") for n in range(8)
    )
    client.force_login(user)

    with django_assert_max_num_queries(7 + SIDEBAR_QUERIES):
        client.get(detail(item))


def test_deleting_user_deletes_their_feedback(user):
    Feedback.objects.create(user=user, text="Пока")

    delete_user_data(user)

    assert not Feedback.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_migration_moves_single_reply_into_thread(user):
    """0003: прежний Feedback.reply становится сообщением администратора.

    transaction=True: откат схемы внутри общей транзакции теста Postgres не
    пускает — у таблицы остаются отложенные проверки FK.
    """
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.migrate([("feedback", "0002_feedbackmessage")])
    apps = executor.loader.project_state([("feedback", "0002_feedbackmessage")]).apps
    OldFeedback = apps.get_model("feedback", "Feedback")
    replied_at = timezone.now() - timedelta(days=3)
    OldFeedback.objects.create(user_id=user.pk, text="Вопрос", reply="Ответ", replied_at=replied_at)
    OldFeedback.objects.create(user_id=user.pk, text="Без ответа")

    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())

    message = FeedbackMessage.objects.get()
    assert (message.feedback.text, message.text, message.from_admin) == ("Вопрос", "Ответ", True)
    assert message.created_at == replied_at


def reply(item, text="Ответ"):
    return FeedbackMessage.objects.create(feedback=item, from_admin=True, text=text)


def has_dot(client):
    return "есть новый ответ" in client.get(reverse("profile")).content.decode()


def test_unread_reply_lights_dot_until_thread_is_opened(client, user):
    item = Feedback.objects.create(user=user, text="Вопрос")
    client.force_login(user)
    assert not has_dot(client)

    reply(item)

    assert has_dot(client)
    assert "Новый ответ" in client.get(URL).content.decode()
    client.get(detail(item))
    assert not has_dot(client)
    assert "Новый ответ" not in client.get(URL).content.decode()


def test_newer_reply_lights_dot_again(client, user):
    item = Feedback.objects.create(user=user, text="Вопрос")
    reply(item)
    client.force_login(user)
    client.get(detail(item))

    reply(item, "Ещё ответ")

    assert has_dot(client)


def test_opening_one_thread_keeps_dot_for_another(client, user):
    first = Feedback.objects.create(user=user, text="Первое")
    second = Feedback.objects.create(user=user, text="Второе")
    reply(first)
    reply(second)
    client.force_login(user)

    client.get(detail(first))

    assert has_dot(client)


def test_own_follow_up_does_not_light_dot(client, user):
    item = Feedback.objects.create(user=user, text="Вопрос")
    client.force_login(user)

    client.post(detail(item), {"text": "Дописка"})

    assert not has_dot(client)


def test_other_users_replies_do_not_light_my_dot(client, user, other_user):
    reply(Feedback.objects.create(user=other_user, text="Чужое"))
    client.force_login(user)

    assert not has_dot(client)


def test_sidebar_shows_dot_and_admin_counter(client, user, admin_user):
    reply(Feedback.objects.create(user=user, text="Моё"))
    Feedback.objects.create(user=user, text="Ещё одно")
    Feedback.objects.create(user=user, text="Закрытое", status="done")

    client.force_login(user)
    html = client.get(reverse("dashboard")).content.decode()
    assert "есть новый ответ" in html
    assert "новых обращений" not in html

    client.force_login(admin_user)
    html = client.get(reverse("dashboard")).content.decode()
    assert "новых обращений: 2" in html
    assert "есть новый ответ" not in html


def test_admin_counter_hidden_when_nothing_is_new(client, admin_user, user):
    Feedback.objects.create(user=user, text="Закрытое", status="done")
    client.force_login(admin_user)

    assert "новых обращений" not in client.get(reverse("profile")).content.decode()
