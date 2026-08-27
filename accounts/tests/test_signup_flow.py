import pytest
from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from django.core import mail
from django.urls import reverse

from accounts.models import User

pytestmark = pytest.mark.django_db

SIGNUP_DATA = {
    "email": "novice@example.com",
    "password1": "correct-horse-battery",
    "password2": "correct-horse-battery",
}


def signup(client):
    return client.post(reverse("account_signup"), SIGNUP_DATA)


def test_signup_creates_unverified_user_and_sends_email(client):
    response = signup(client)

    assert response.status_code == 302
    email_address = EmailAddress.objects.get(email=SIGNUP_DATA["email"])
    assert email_address.verified is False
    assert len(mail.outbox) == 1
    assert "Подтвердите email" in mail.outbox[0].subject


def test_login_is_blocked_until_email_is_confirmed(client):
    signup(client)
    client.logout()

    response = client.post(
        reverse("account_login"),
        {"login": SIGNUP_DATA["email"], "password": SIGNUP_DATA["password1"]},
    )

    assert response.status_code == 302
    assert reverse("account_email_verification_sent") in response["Location"]
    assert client.get(reverse("dashboard")).status_code == 302


def test_login_works_after_email_confirmation(client):
    signup(client)
    client.logout()
    email_address = EmailAddress.objects.get(email=SIGNUP_DATA["email"])
    key = EmailConfirmationHMAC(email_address).key

    client.post(reverse("account_confirm_email", args=[key]))
    email_address.refresh_from_db()
    assert email_address.verified is True

    client.logout()
    response = client.post(
        reverse("account_login"),
        {"login": SIGNUP_DATA["email"], "password": SIGNUP_DATA["password1"]},
    )

    assert response.status_code == 302
    assert client.get(reverse("dashboard")).status_code == 200


def test_signup_with_filled_honeypot_creates_no_user(client):
    response = client.post(
        reverse("account_signup"),
        SIGNUP_DATA | {"phone_number": "+7 900 000-00-00"},
    )

    assert response.status_code == 302
    assert not User.objects.filter(email=SIGNUP_DATA["email"]).exists()
    assert len(mail.outbox) == 0


def test_password_reset_sends_email_with_russian_subject(client, user):
    response = client.post(reverse("account_reset_password"), {"email": user.email})

    assert response.status_code == 302
    assert len(mail.outbox) == 1
    assert "Сброс пароля" in mail.outbox[0].subject


def test_repeat_signup_sends_account_exists_email_in_russian(client, user):
    response = client.post(
        reverse("account_signup"),
        {
            "email": user.email,
            "password1": "correct-horse-battery",
            "password2": "correct-horse-battery",
        },
    )

    assert response.status_code == 302
    assert len(mail.outbox) == 1
    assert "Аккаунт уже существует" in mail.outbox[0].subject
    assert "уже есть" in mail.outbox[0].body


def test_notification_emails_are_in_russian(client, user, password):
    client.force_login(user)

    response = client.post(
        reverse("account_change_password"),
        {
            "oldpassword": password,
            "password1": "brand-new-horse-42",
            "password2": "brand-new-horse-42",
        },
    )

    assert response.status_code == 302
    assert [m.subject for m in mail.outbox] == ["Пароль изменён — Дневник тренировок"]
    assert "Пароль изменён." in mail.outbox[0].body
