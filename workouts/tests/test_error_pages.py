"""Страницы ошибок: русские, в оформлении приложения и без зависимостей у 500-й."""

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from workouts.tests.factories import WorkoutFactory

pytestmark = pytest.mark.django_db


def test_unknown_url_renders_custom_404(client, user):
    client.force_login(user)

    response = client.get("/nope/")

    assert response.status_code == 404
    assert "404.html" in [t.name for t in response.templates]
    assert "Страница не найдена" in response.content.decode()


def test_foreign_record_renders_custom_404(client, user, other_user):
    """404 — штатный ответ правила изоляции, и он должен выглядеть как приложение."""
    alien = WorkoutFactory(user=other_user)

    client.force_login(user)
    response = client.get(reverse("workout_summary", args=[alien.pk]))

    assert response.status_code == 404
    assert "На дашборд" in response.content.decode()


def test_server_error_page_renders_without_context():
    """500-я рендерится без request и контекста — так её вызывает Django."""
    html = render_to_string("500.html")

    assert "Что-то сломалось" in html
    assert "{% static" not in html
    assert "/static/" not in html  # ни одного файла: манифест мог бы их не отдать


def test_csrf_failure_page_is_russian(user):
    """Истёкший CSRF на форме — частый кейс для установленного PWA."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    response = client.post(reverse("profile_rest"), {"delta": "15"})

    assert response.status_code == 403
    assert "Страница устарела" in response.content.decode()


def test_manifest_is_served_for_pwa(client):
    """Манифест — вьюха: правильный Content-Type и иконки через static."""
    response = client.get(reverse("manifest"))

    content = response.content.decode()
    assert response.status_code == 200
    assert response["Content-Type"] == "application/manifest+json"
    assert '"id": "/"' in content
    assert "icon-maskable-512.png" in content
    assert '"purpose": "maskable"' in content


def test_favicon_redirects_to_icon(client):
    """Браузеры просят /favicon.ico независимо от <link>."""
    response = client.get("/favicon.ico")

    assert response.status_code == 301
    assert "icon-192.png" in response.url
