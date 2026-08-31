"""Служебные ответы уровня проекта: манифест PWA и иконки в корне сайта."""

from django.contrib.staticfiles import finders
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.templatetags.static import static

# Год: арт меняется вместе с релизом, а по этим путям имя файла без хэша.
ICON_CACHE_CONTROL = "public, max-age=31536000"


def manifest(request):
    """manifest.webmanifest шаблоном, а не файлом в static/.

    Так иконки идут через {% static %} (в проде — с хэшем в имени, поэтому смена
    арта доходит до установленных копий), а Content-Type получается правильный.
    """
    return render(request, "manifest.json", content_type="application/manifest+json")


def favicon(request):
    """Браузеры просят /favicon.ico независимо от <link> — отдаём PNG-иконку."""
    return redirect(static("img/icon-192.png"), permanent=True)


def apple_touch_icon(request):
    """Иконка по корневому пути — вторая дорожка, которой пользуется iOS.

    Когда iOS не может взять значок из <link rel="apple-touch-icon">, он просто
    просит /apple-touch-icon.png и /apple-touch-icon-precomposed.png у корня
    сайта. У нас там было 404, и на рабочем столе оказывалась серая плитка с
    буквой. Отдаём файл напрямую, а не редиректом: редирект на хэшированный
    адрес iOS может не пройти.
    """
    path = finders.find("img/apple-touch-icon-180.png")
    if path is None:  # pragma: no cover — файл лежит в репозитории
        raise Http404("Иконка не найдена")
    response = FileResponse(open(path, "rb"), content_type="image/png")
    response["Cache-Control"] = ICON_CACHE_CONTROL
    return response
