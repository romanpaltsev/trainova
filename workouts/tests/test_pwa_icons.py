"""Иконки PWA: требования Android и iOS различаются, и их легко нарушить молча.

iOS ставит значок на рабочий стол только из apple-touch-icon и ждёт 180×180 без
альфа-канала. Нарушишь — вместо значка на рабочем столе окажется скриншот
страницы, и узнаешь об этом только с телефона в руках.
"""

import json
import re
import struct
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.urls import reverse

pytestmark = pytest.mark.django_db

APPLE_ICON = "img/apple-touch-icon-180.png"


def png_header(path):
    """(ширина, высота, тип цвета) из IHDR. Тип 6 — RGBA, 2 — RGB без альфа-канала."""
    data = Path(path).read_bytes()
    width, height, _, color_type = struct.unpack(">IIBB", data[16:26])
    return width, height, color_type


def head_links(client):
    html = client.get(reverse("account_login")).content.decode()
    return re.findall(r"<link[^>]*>", html, re.S)


def test_apple_touch_icon_is_opaque_180(client):
    """Главное требование iOS: 180×180 и без альфа-канала."""
    path = finders.find(APPLE_ICON)

    assert path, f"{APPLE_ICON} нет в static — iOS останется без значка"
    width, height, color_type = png_header(path)
    assert (width, height) == (180, 180)
    assert color_type == 2, "PNG с альфа-каналом iOS может проигнорировать"


def test_apple_touch_icon_is_linked_in_head(client):
    links = [link for link in head_links(client) if "apple-touch-icon" in link]

    assert links, "без <link rel=apple-touch-icon> iOS покажет скриншот страницы"
    assert 'sizes="180x180"' in links[0]
    assert "apple-touch-icon-180.png" in links[0]


def test_home_screen_name_is_trainova(client):
    """Ярлык подписывается коротким латинским именем — под иконкой мало места.

    Проверяются оба места сразу: iOS читает meta, Android и режим
    веб-приложения iOS 18 — манифест. Разъедутся — на разных телефонах у
    одного приложения окажутся разные имена.
    """
    manifest = json.loads(client.get(reverse("manifest")).content.decode())
    links = client.get(reverse("account_login")).content.decode()

    assert manifest["name"] == "Trainova"
    assert manifest["short_name"] == "Trainova"
    assert '<meta name="apple-mobile-web-app-title" content="Trainova">' in links
    # А тексты интерфейса остаются русскими.
    assert "Дневник тренировок" in links


def test_manifest_icons_are_png_only(client):
    """SVG в манифесте iOS не растеризует, поэтому список — только растровый."""
    manifest = json.loads(client.get(reverse("manifest")).content.decode())

    assert manifest["icons"], "манифест без иконок"
    for icon in manifest["icons"]:
        assert icon["type"] == "image/png", f"{icon['src']} — не PNG"
        assert icon["src"].endswith(".png")


def test_all_referenced_icons_exist(client):
    """Каждая иконка из манифеста и из head реально лежит в static."""
    manifest = json.loads(client.get(reverse("manifest")).content.decode())
    sources = [icon["src"] for icon in manifest["icons"]]
    sources += re.findall(r'href="([^"]*/static/[^"]*)"', " ".join(head_links(client)))

    for src in sources:
        relative = src.removeprefix(settings.STATIC_URL).removeprefix("/static/")
        assert finders.find(relative), f"{src} не найден в static"


def test_maskable_icon_is_opaque(client):
    """У maskable-иконки фон залит целиком: под маской лончера углы просвечивали бы."""
    width, height, color_type = png_header(finders.find("img/icon-maskable-512.png"))

    assert (width, height) == (512, 512)
    assert color_type == 2


def test_no_raster_icon_has_alpha_channel(client):
    """Ни одна растровая иконка не должна быть прозрачной.

    Прозрачную iOS молча игнорирует, а скругление всё равно накладывает
    платформа. Скруглённая версия живёт в векторе — icon.svg.
    """
    for name in ("img/icon-192.png", "img/icon-512.png", "img/icon-maskable-512.png", APPLE_ICON):
        _, _, color_type = png_header(finders.find(name))

        assert color_type == 2, f"{name} с альфа-каналом"


@pytest.mark.parametrize("path", ["/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"])
def test_icon_is_served_from_site_root(client, path):
    """Вторая дорожка iOS: когда <link> не сработал, он просит иконку у корня."""
    response = client.get(path)

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    body = b"".join(response.streaming_content)
    assert body[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(body) == Path(finders.find(APPLE_ICON)).stat().st_size
