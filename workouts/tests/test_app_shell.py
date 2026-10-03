"""Каркас ПК: боковая панель разделов и поиск в шапке.

Панель рендерится на каждой странице приложения, поэтому её ошибки видны сразу
везде: подсвечен не тот пункт, панель показалась гостю, состояние «свёрнута»
мигает при переходах. Тесты ниже держат именно это.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from workouts.tests.factories import BodyMetricFactory

pytestmark = pytest.mark.django_db


def active_items(html):
    """Подписи подсвеченных пунктов панели (title ссылки)."""
    return re.findall(r'class="app-side-link is-active"[^>]*title="([^"]+)"', html)


def page(client, url_name, *args):
    return client.get(reverse(url_name, args=args)).content.decode()


def test_sidebar_is_rendered_for_signed_in_user(client, user):
    client.force_login(user)

    html = page(client, "dashboard")

    assert 'class="app-sidebar"' in html
    assert "app-bottom-nav" in html  # телефонная навигация на месте: панель её не заменяет


def test_guest_page_has_no_sidebar(client):
    """Гостю разделы приложения не показываем: 404 рендерится той же базой."""
    html = client.get("/nope/").content.decode()

    # Сам ключ «app-sidebar» в <head> есть у всех — скрипт темы общий, — поэтому
    # ищем разметку, а не подстроку.
    assert 'class="app-sidebar"' not in html
    assert 'class="app-header-search"' not in html


@pytest.mark.parametrize(
    ("url_name", "expected"),
    [
        ("dashboard", "Дашборд"),
        ("workout_history", "История"),
        ("exercise_list", "Упражнения"),
        ("profile", "Профиль"),
        ("my_locations", "Мои места"),
        ("my_sports", "Мои виды спорта"),
        ("body_measurements", "Мои замеры"),
        ("data_transfer", "Экспорт и импорт"),
        ("changelog", "Что нового"),
        # Страницы аккаунта открываются из профиля — за ним и числятся.
        ("account_change_password", "Профиль"),
    ],
)
def test_sidebar_highlights_exactly_current_section(client, user, url_name, expected):
    """У пунктов «Моё» общий nav_active, поэтому подсветка идёт по имени маршрута:
    иначе на «Моих местах» горела бы вся группа."""
    client.force_login(user)

    assert active_items(page(client, url_name)) == [expected]


def test_metric_page_keeps_measurements_highlighted(client, user):
    """Страница параметра — часть «Моих замеров»: подсветка идёт по префиксу
    маршрута body_, а не по точному имени, иначе на ней не горело бы ничего."""
    metric = BodyMetricFactory()
    client.force_login(user)

    assert active_items(page(client, "body_metric", metric.pk)) == ["Мои замеры"]


def test_header_search_queries_exercise_catalog(client, user):
    """Поиск в шапке — обычная GET-форма каталога: параметр тот же, что у поиска
    на странице справочника, поэтому каталог его уже понимает."""
    client.force_login(user)

    html = page(client, "dashboard")
    form = re.search(r'<form class="app-header-search"[^>]*>', html).group(0)

    assert f'action="{reverse("exercise_list")}"' in form
    assert 'method="get"' in form
    assert 'name="q"' in html.split('class="app-header-search"', 1)[1].split("</form>", 1)[0]


def test_collapsed_sidebar_is_applied_before_first_paint(client, user):
    """Состояние «свёрнута» выставляется скриптом в <head>, а меняется shell.js —
    оба обязаны читать один ключ localStorage, иначе выбор забывался бы и
    развёрнутая панель мигала бы на каждом переходе."""
    client.force_login(user)

    head = page(client, "dashboard").split("</head>", 1)[0]
    shell_js = (Path(settings.BASE_DIR) / "static/js/shell.js").read_text()
    key = re.search(r'const KEY = "([^"]+)"', shell_js).group(1)

    assert f'localStorage.getItem("{key}")' in head
    assert "data-sidebar-collapsed" in head
