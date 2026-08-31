"""Генератор растровых иконок PWA — единственный источник для static/img.

Три разных требования, поэтому три вида файлов:

- `icon-192/512.png` — скруглённый квадрат с прозрачными углами: так иконку ждут
  Chrome и десктопные браузеры.
- `icon-maskable-512.png` — фон залит целиком, глиф сжат в центральные 80%
  (safe zone спецификации): под маской лончера Android углы просвечивали бы.
- `apple-touch-icon-180.png` — **180×180 и без альфа-канала**: iOS ставит иконку на
  рабочий стол только из apple-touch-icon, свою маску накладывает сам, а PNG с
  прозрачностью и нестандартного размера может проигнорировать — тогда на рабочем
  столе вместо значка оказывается скриншот страницы.

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
CORNER_RADIUS = 14.0  # в тех же координатах 64×64, как у rect в icon.svg

# (файл, размер, масштаб глифа, прозрачные углы)
ICONS = [
    ("icon-192.png", 192, 1.0, True),
    ("icon-512.png", 512, 1.0, True),
    ("icon-maskable-512.png", 512, 0.8, False),
    ("apple-touch-icon-180.png", 180, 0.8, False),
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


def glyph_segments(glyph_scale):
    """Отрезки глифа в координатах 64×64, сжатые к центру до safe zone."""
    segments = [(x, y1, x, y2) for x, y1, y2 in BARS]
    x1, x2, y = CROSSBAR
    segments.append((x1, y, x2, y))
    return [
        (
            32 + (ax - 32) * glyph_scale,
            32 + (ay - 32) * glyph_scale,
            32 + (bx - 32) * glyph_scale,
            32 + (by - 32) * glyph_scale,
        )
        for ax, ay, bx, by in segments
    ]


def inside_rounded_square(gx, gy):
    """Точка внутри скруглённого квадрата 64×64 — для иконок с прозрачными углами."""
    cx = min(max(gx, CORNER_RADIUS), 64 - CORNER_RADIUS)
    cy = min(max(gy, CORNER_RADIUS), 64 - CORNER_RADIUS)
    return (gx - cx) ** 2 + (gy - cy) ** 2 <= CORNER_RADIUS**2


def render(size, glyph_scale, transparent_corners):
    """Строки пикселей: RGBA для иконок с углами, RGB для непрозрачных."""
    segments = glyph_segments(glyph_scale)
    radius = STROKE * glyph_scale / 2
    rows = []
    for y in range(size):
        row = bytearray()
        gy = (y + 0.5) * 64 / size
        for x in range(size):
            gx = (x + 0.5) * 64 / size
            if transparent_corners and not inside_rounded_square(gx, gy):
                row.extend((0, 0, 0, 0))
                continue
            on_glyph = any(
                capsule_distance(gx, gy, ax, ay, bx, by) <= radius for ax, ay, bx, by in segments
            )
            row.extend(ACCENT if on_glyph else BG)
            if transparent_corners:
                row.append(0xFF)
        rows.append(bytes(row))
    return rows


def write_png(path, rows, size, *, with_alpha):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + row for row in rows)  # 0 = фильтр «без предсказания»
    color_type = 6 if with_alpha else 2  # 6 = RGBA, 2 = RGB без альфа-канала
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, color_type, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


if __name__ == "__main__":
    for name, size, glyph_scale, transparent in ICONS:
        path = IMG_DIR / name
        write_png(
            path,
            render(size, glyph_scale, transparent),
            size,
            with_alpha=transparent,
        )
        alpha = "с прозрачными углами" if transparent else "непрозрачная"
        print(f"{name}: {size}×{size}, {alpha}, {path.stat().st_size} байт")
