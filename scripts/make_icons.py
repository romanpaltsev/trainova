"""Генератор растровых иконок PWA — единственный источник для static/img.

Все растровые иконки — **непрозрачный квадрат во всё поле**, глиф сжат в
центральные 80% (safe zone спецификации maskable). Скругление накладывает сама
платформа: Android — маской лончера, iOS — своей суперэллипсной маской.

Почему без альфа-канала даже там, где раньше были прозрачные углы: iOS ставит
значок на рабочий стол только из PNG и прозрачную иконку может молча
проигнорировать — тогда вместо значка появляется серая плитка с первой буквой
названия. Один непрозрачный арт снимает этот вопрос на всех дорожках сразу:
apple-touch-icon, иконки манифеста и корневой /apple-touch-icon.png.

Скруглённая версия осталась в векторе (static/img/icon.svg) — её показывают
браузеры на вкладке и в закладках, где никакой маски нет.

PNG пишем вручную (zlib + struct): графических зависимостей в проекте нет и по
CLAUDE.md добавлять их нельзя. Запуск: uv run python scripts/make_icons.py
"""

import struct
import zlib
from pathlib import Path

IMG_DIR = Path(__file__).resolve().parent.parent / "static" / "img"

# Дубль токенов --app-bg (тёмная тема) и --app-accent из static/css/tokens.css:
# PNG не понимает CSS-переменных.
BG = (0x0D, 0x10, 0x15)
ACCENT = (0xC6, 0xF1, 0x3E)

# Глиф гантели в координатах 64×64 — те же, что в static/img/icon.svg.
STROKE = 5.0
BARS = [  # (x, y1, y2) — вертикальные «капсулы»
    (16.0, 22.0, 42.0),
    (25.0, 17.0, 47.0),
    (39.0, 17.0, 47.0),
    (48.0, 22.0, 42.0),
]
CROSSBAR = (25.0, 39.0, 32.0)  # (x1, x2, y) — перекладина
GLYPH_SCALE = 0.8  # safe zone maskable — центральные 80%

# Арт у всех файлов одинаковый, различаются только размеры. maskable-версия
# лежит отдельным файлом, потому что манифест обязан указать purpose явно.
ICONS = [
    ("icon-192.png", 192),
    ("icon-512.png", 512),
    ("icon-maskable-512.png", 512),
    ("apple-touch-icon-180.png", 180),
]


def capsule_distance(px, py, x1, y1, x2, y2):
    """Расстояние от точки до отрезка — так рисуются линии со скруглёнными концами."""
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_sq))
    nx, ny = x1 + t * dx, y1 + t * dy
    return ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5


def glyph_segments():
    """Отрезки глифа в координатах 64×64, сжатые к центру до safe zone."""
    segments = [(x, y1, x, y2) for x, y1, y2 in BARS]
    x1, x2, y = CROSSBAR
    segments.append((x1, y, x2, y))
    return [
        (
            32 + (ax - 32) * GLYPH_SCALE,
            32 + (ay - 32) * GLYPH_SCALE,
            32 + (bx - 32) * GLYPH_SCALE,
            32 + (by - 32) * GLYPH_SCALE,
        )
        for ax, ay, bx, by in segments
    ]


def render(size):
    """Строки пикселей RGB: фон во всё поле, поверх — глиф гантели."""
    segments = glyph_segments()
    radius = STROKE * GLYPH_SCALE / 2
    rows = []
    for y in range(size):
        row = bytearray()
        gy = (y + 0.5) * 64 / size
        for x in range(size):
            gx = (x + 0.5) * 64 / size
            on_glyph = any(
                capsule_distance(gx, gy, ax, ay, bx, by) <= radius for ax, ay, bx, by in segments
            )
            row.extend(ACCENT if on_glyph else BG)
        rows.append(bytes(row))
    return rows


def write_png(path, rows, size):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + row for row in rows)  # 0 = фильтр «без предсказания»
    png = b"\x89PNG\r\n\x1a\n"
    # Тип цвета 2 = RGB без альфа-канала.
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


if __name__ == "__main__":
    for name, size in ICONS:
        path = IMG_DIR / name
        write_png(path, render(size), size)
        print(f"{name}: {size}×{size}, без альфа-канала, {path.stat().st_size} байт")
