"""Служебные ответы уровня проекта: манифест PWA."""

from django.shortcuts import redirect, render
from django.templatetags.static import static


def manifest(request):
    """manifest.webmanifest шаблоном, а не файлом в static/.

    Так иконки идут через {% static %} (в проде — с хэшем в имени, поэтому смена
    арта доходит до установленных копий), а Content-Type получается правильный.
    """
    return render(request, "manifest.json", content_type="application/manifest+json")


def favicon(request):
    """Браузеры просят /favicon.ico независимо от <link> — отдаём PNG-иконку."""
    return redirect(static("img/icon-192.png"), permanent=True)
