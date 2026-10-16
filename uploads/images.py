"""Обработка загруженного изображения: проверить, развернуть, пережать.

Браузер обычно уже ужал снимок (static/js/image-resize.js), но сервер ему не
верит: файл мог прийти без JS, из старого браузера или руками. Поэтому каждое
изображение открывает Pillow, поворачивает по EXIF-ориентации и пережимает в
JPEG заново — без метаданных: в EXIF снимка с телефона лежат координаты места
съёмки, а фото общей записи справочника видят все.
"""

import uuid
from dataclasses import dataclass
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

# Предел тела запроса у nginx (client_max_body_size 4m): больше всё равно не
# доедет, а сказать об этом человеку должна форма, а не страница nginx.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
# Пикселей в исходнике: 50 Мп — с запасом больше камеры любого телефона. Больше —
# либо не фото, либо «бомба», которая в памяти развернётся в гигабайты.
MAX_SOURCE_PIXELS = 50_000_000
# HEIC Pillow без плагина не читает; с iPhone он приходит уже JPEG — его
# перекодирует браузер (Safari при выборе файла или наш image-resize.js).
FORMATS = {"JPEG", "PNG", "WEBP"}
# Длинная сторона: крупный просмотр и миниатюра для списков (вдвое больше
# самой крупной ячейки — на экранах с плотностью 2×).
FULL_SIZE = 1600
THUMB_SIZE = 320
JPEG_QUALITY = 82

UNREADABLE = "Не получилось прочитать фото. Подойдёт JPEG, PNG или WebP."


@dataclass(frozen=True)
class ProcessedImage:
    """Готовые к сохранению JPEG: крупный и миниатюра с общим случайным именем."""

    full: ContentFile
    thumb: ContentFile


def process_image(uploaded):
    """Проверить и пережать загруженный файл; ошибка — ValidationError для формы."""
    if uploaded.size > MAX_UPLOAD_BYTES:
        raise ValidationError("Файл больше 4 МБ — уменьшите фото.", code="too_large")
    uploaded.seek(0)
    try:
        with Image.open(uploaded) as source:
            if source.format not in FORMATS:
                raise ValidationError(UNREADABLE, code="format")
            if source.width * source.height > MAX_SOURCE_PIXELS:
                raise ValidationError("Слишком большое изображение.", code="too_many_pixels")
            icc_profile = source.info.get("icc_profile")
            image = flatten(ImageOps.exif_transpose(source))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        # Битый или обрезанный файл, «бомба» сверх предела Pillow, режим, который
        # не переводится в RGB, — для человека всё это одно: фото не читается.
        raise ValidationError(UNREADABLE, code="unreadable") from error
    full = image.copy()
    full.thumbnail((FULL_SIZE, FULL_SIZE), Image.Resampling.LANCZOS)
    thumb = full.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    stem = uuid.uuid4().hex
    return ProcessedImage(
        full=encode(full, f"{stem}.jpg", icc_profile),
        thumb=encode(thumb, f"{stem}-thumb.jpg", icc_profile),
    )


def flatten(image):
    """RGB без прозрачности: у JPEG её нет, поэтому прозрачный фон — белый."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def encode(image, name, icc_profile):
    """JPEG без EXIF; цветовой профиль остаётся — у снимков iPhone он Display P3,
    и без него цвета поблекли бы."""
    buffer = BytesIO()
    image.save(
        buffer,
        "JPEG",
        quality=JPEG_QUALITY,
        optimize=True,
        progressive=True,
        icc_profile=icc_profile,
    )
    return ContentFile(buffer.getvalue(), name=name)
