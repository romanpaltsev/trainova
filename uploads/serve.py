"""Отдать загруженный файл — только тому, кто видит его запись.

Ни nginx, ни whitenoise загруженное не отдают: у своей записи фото видит
только хозяин. Поэтому адрес ведёт во вьюху фичи, она передаёт сюда строки,
которые человек видит (тем же queryset, что и страницы), и файл отдаётся,
только если его имя записано в одной из них. Имя из адреса лишь сравнивается
с базой: подсунуть путь вне хранилища нечем, открывается то, что записано.
"""

import mimetypes

from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import FileResponse, Http404

# Имя файла уникально на каждую загрузку, поэтому содержимое по адресу не
# меняется никогда, и браузер может год не переспрашивать. private — фото своей
# записи не должны оседать в общих кэшах по дороге.
CACHE_CONTROL = "private, max-age=31536000, immutable"


def serve_file(queryset, fields, name):
    """FileResponse с файлом name, если он записан в одном из fields у строки
    queryset; иначе 404 — и для чужого, и для несуществующего."""
    if not name:
        # Пустое имя совпало бы со всеми строками без файла.
        raise Http404
    lookup = Q()
    for field in fields:
        lookup |= Q(**{field: name})
    if not queryset.filter(lookup).exists():
        raise Http404
    try:
        handle = default_storage.open(name, "rb")
    except FileNotFoundError:
        raise Http404 from None
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    response = FileResponse(handle, content_type=content_type)
    response["Cache-Control"] = CACHE_CONTROL
    return response
