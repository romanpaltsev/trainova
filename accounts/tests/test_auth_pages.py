import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("url_name", "template"),
    [
        ("account_login", "account/login.html"),
        ("account_signup", "account/signup.html"),
        ("account_reset_password", "account/password_reset.html"),
    ],
)
def test_anonymous_auth_pages_render(client, url_name, template):
    response = client.get(reverse(url_name))

    assert response.status_code == 200
    assert template in [t.name for t in response.templates]


def test_password_change_requires_login(client):
    response = client.get(reverse("account_change_password"))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_password_change_renders_for_logged_in_user(client, user, password):
    client.force_login(user)

    response = client.get(reverse("account_change_password"))

    assert response.status_code == 200
    assert "account/password_change.html" in [t.name for t in response.templates]


def test_dashboard_requires_login(client):
    response = client.get(reverse("dashboard"))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_dashboard_renders_for_logged_in_user(client, user):
    client.force_login(user)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert "Дашборд" in response.content.decode()
