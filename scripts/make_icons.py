"""Генератор maskable-иконки PWA.

Обычная иконка — скруглённый квадрат с прозрачными углами: под квадратной маской
лончера Android углы просвечивали бы. Maskable-версия заливает фон целиком, а глиф
уменьшен внутрь центральных 80% (safe zone спецификации).

PNG пишем вручную (zlib + struct): графических зависимостей в проекте нет и по
CLAUDE.md добавлять их нельзя. Запуск: uv run python scripts/make_icons.py
"""

import struct
import zlib
from pathlib import Path

SIZE = 512
OUT = Path(__file__).resolve().parent.parent / "static" / "img" / "icon-maskable-512.png"

# Дубль токенов --app-bg (тёмная тема) и --app-accent из static/css/tokens.css:
# PNG не понимает CSS-переменных.
BG = (0x0D, 0x10, 0x15, 0xFF)
ACCENT = (0xC6, 0xF1, 0x3E, 0xFF)

# Глиф гантели в координатах 64×64 (как static/img/icon.svg), но сжатый к центру:
# safe zone maskable — центральные 80%, поэтому масштаб 0.8 от исходного.
STROKE = 5.0
BARS = [  # (x, y1, y2) — вертикальные «капсулы»
    (16.0, 22.0, 42.0),
    (25.0, 17.0, 47.0),
    (39.0, 17.0, 47.0),
    (48.0, 22.0, 42.0),
]
CROSSBAR = (25.0, 39.0, 32.0)  # (x1, x2, y) — перекладина


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
    scaled = []
    for ax, ay, bx, by in segments:
        # Сжатие к центру (32, 32) с коэффициентом 0.8.
        scaled.append(
            (
                32 + (ax - 32) * 0.8,
                32 + (ay - 32) * 0.8,
                32 + (bx - 32) * 0.8,
                32 + (by - 32) * 0.8,
            )
        )
    return scaled


def render():
    segments = glyph_segments()
    scale = SIZE / 64
    radius = STROKE * 0.8 / 2 * scale
    rows = []
    for y in range(SIZE):
        row = bytearray()
        gy = (y + 0.5) / scale
        for x in range(SIZE):
            gx = (x + 0.5) / scale
            inside = any(
                capsule_distance(
                    gx * scale, gy * scale, ax * scale, ay * scale, bx * scale, by * scale
                )
                <= radius
                for ax, ay, bx, by in segments
            )
            row.extend(ACCENT if inside else BG)
        rows.append(bytes(row))
    return rows


def write_png(path, rows):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + row for row in rows)  # 0 = фильтр «без предсказания»
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


if __name__ == "__main__":
    write_png(OUT, render())
    print(f"{OUT} — {OUT.stat().st_size} байт")
