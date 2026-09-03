"""Генератор растровой графики PWA — единственный источник для static/img.

Здесь два набора: иконки рабочего стола и splash-экраны запуска для iOS.

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

Про splash-экраны (SPLASHES ниже). iOS в режиме standalone не умеет строить
заставку из манифеста: background_color оттуда читает Android, а Safari — нет.
Без apple-touch-startup-image всё время загрузки показывается белым, поэтому
медленный старт выглядит не как «грузится», а как «сломалось».

Картинку iOS выбирает точным совпадением медиазапроса с размерами устройства и
его плотностью — «примерно подходящую» он не возьмёт и вернётся к белому. Отсюда
файл на каждую геометрию, а не один универсальный.
"""

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMG_DIR = ROOT / "static" / "img"
SPLASH_TEMPLATE = ROOT / "templates" / "includes" / "apple_splash.html"

# Дубль токенов --app-bg (тёмная тема) и --app-accent из static/css/tokens.css:
# PNG не понимает CSS-переменных.
BG = (0x0D, 0x10, 0x15)
ACCENT = (0xC6, 0xF1, 0x3E)

# Те же роли для светлой темы: --app-bg и --app-text. Глиф здесь берёт цвет
# текста, а не акцент: лаймовый #C6F13E на почти белом фоне почти не виден,
# и заставка выглядела бы пустой.
BG_LIGHT = (0xF6, 0xF7, 0xF9)
FG_LIGHT = (0x16, 0x20, 0x2B)

THEMES = {  # имя файла → (фон, цвет глифа, значение prefers-color-scheme)
    "dark": (BG, ACCENT, "dark"),
    "light": (BG_LIGHT, FG_LIGHT, "light"),
}

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

# Модели iPhone: (ширина и высота в CSS-пикселях, плотность). Ориентация в
# медиазапросе не нужна — device-width и device-height iOS сообщает по самому
# устройству, а не по текущему повороту, и приложение всё равно portrait.
#
# Одна строка обслуживает несколько моделей с одинаковой геометрией (в
# комментарии — какие). iPad в наборе нет намеренно: главный сценарий — телефон
# в зале, а каждый размер стоит двух файлов. Заведётся iPad — добавить строку.
SPLASHES = [
    (375, 667, 2),  # SE 2/3, 8
    (414, 896, 2),  # XR, 11
    (375, 812, 3),  # X, XS, 11 Pro, 12 mini, 13 mini
    (414, 896, 3),  # XS Max, 11 Pro Max
    (390, 844, 3),  # 12, 13, 14
    (428, 926, 3),  # 12/13 Pro Max, 14 Plus
    (393, 852, 3),  # 14 Pro, 15, 15 Pro, 16
    (430, 932, 3),  # 14 Pro Max, 15 Plus, 15 Pro Max, 16 Plus
    (402, 874, 3),  # 16 Pro
    (440, 956, 3),  # 16 Pro Max
]

# Доля короткой стороны, которую занимает глиф. На иконке он во всё поле, а на
# заставке такой же крупный выглядел бы заслонкой: это фон с логотипом, а не
# картинка во весь экран.
SPLASH_GLYPH_FRACTION = 0.22


def capsule_distance(px, py, x1, y1, x2, y2):
    """Расстояние от точки до отрезка — так рисуются линии со скруглёнными концами."""
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_sq))
    nx, ny = x1 + t * dx, y1 + t * dy
    return ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5


def glyph_segments(scale=GLYPH_SCALE):
    """Отрезки глифа в координатах 64×64, сжатые к центру с заданным масштабом.

    Иконка сжимается до safe zone (0.8), заставке это не нужно: маску там никто
    не накладывает, и глиф уже уменьшен своей долей экрана.
    """
    segments = [(x, y1, x, y2) for x, y1, y2 in BARS]
    x1, x2, y = CROSSBAR
    segments.append((x1, y, x2, y))
    return [
        (
            32 + (ax - 32) * scale,
            32 + (ay - 32) * scale,
            32 + (bx - 32) * scale,
            32 + (by - 32) * scale,
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


def render_splash(width, height, bg, fg):
    """Строки пикселей RGB: однотонный фон и глиф гантели по центру.

    Считаются только пиксели внутри квадрата с глифом, остальные строки —
    готовая байтовая строка фона: на 1320×2868 полный проход по пикселям на
    питоне без графических библиотек шёл бы минуты.

    Глиф сглажен усреднением 2×2 подпикселей — в отличие от иконок, где жёсткий
    край незаметен на 180 пикселях. Здесь скруглённые концы капсул крупные, и
    ступеньки на них видно. Сглаживание смешивает цвета фона и глифа, альфа-канал
    для этого не нужен: PNG остаётся RGB, как требует iOS.
    """
    box = round(min(width, height) * SPLASH_GLYPH_FRACTION)
    left, top = (width - box) // 2, (height - box) // 2
    segments = glyph_segments(scale=1.0)
    radius = STROKE / 2
    background = bytes(bg) * width
    samples = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))

    rows = []
    for y in range(height):
        if not top <= y < top + box:
            rows.append(background)
            continue
        middle = bytearray()
        for x in range(left, left + box):
            covered = 0
            for offset_x, offset_y in samples:
                gx = (x - left + offset_x) * 64 / box
                gy = (y - top + offset_y) * 64 / box
                if any(
                    capsule_distance(gx, gy, ax, ay, bx, by) <= radius
                    for ax, ay, bx, by in segments
                ):
                    covered += 1
            if covered == 0:
                middle.extend(bg)
            elif covered == len(samples):
                middle.extend(fg)
            else:
                weight = covered / len(samples)
                middle.extend(
                    round(back + (front - back) * weight)
                    for back, front in zip(bg, fg, strict=True)
                )
        rows.append(bytes(bg) * left + bytes(middle) + bytes(bg) * (width - left - box))
    return rows


def write_png(path, rows, width, height=None):
    """height=None — квадрат: у иконок ширина и высота всегда совпадают."""
    height = width if height is None else height

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + row for row in rows)  # 0 = фильтр «без предсказания»
    png = b"\x89PNG\r\n\x1a\n"
    # Тип цвета 2 = RGB без альфа-канала.
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def splash_name(width_px, height_px, theme):
    return f"splash-{width_px}x{height_px}-{theme}.png"


def write_splash_template(entries):
    """Собрать шаблон со ссылками из того же списка, что и сами файлы.

    Двадцать медиазапросов, набранных руками, разъедутся с именами файлов — а
    ошибка тихая: iOS просто вернётся к белому экрану, и заметить это можно
    только с телефона в руках. Поэтому шаблон генерируется, а не пишется.
    """
    lines = [
        "{% load static %}",
        "{% comment %}",
        "  СГЕНЕРИРОВАН scripts/make_icons.py — правьте SPLASHES там, не здесь.",
        "",
        "  Заставка запуска для iOS в режиме standalone: без неё всё время",
        "  загрузки показывается белым, и медленный старт выглядит поломкой.",
        "  Картинку iOS берёт только по точному совпадению размеров устройства",
        "  и плотности, поэтому файл на каждую геометрию, а не один общий.",
        "{% endcomment %}",
    ]
    for width, height, ratio in entries:
        for theme, (_, _, scheme) in THEMES.items():
            name = splash_name(width * ratio, height * ratio, theme)
            lines.append(
                '<link rel="apple-touch-startup-image"'
                f' media="(device-width: {width}px) and (device-height: {height}px)'
                f" and (-webkit-device-pixel-ratio: {ratio})"
                f' and (prefers-color-scheme: {scheme})"'
                f" href=\"{{% static 'img/{name}' %}}\">"
            )
    SPLASH_TEMPLATE.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    for name, size in ICONS:
        path = IMG_DIR / name
        write_png(path, render(size), size)
        print(f"{name}: {size}×{size}, без альфа-канала, {path.stat().st_size} байт")

    for width, height, ratio in SPLASHES:
        width_px, height_px = width * ratio, height * ratio
        for theme, (bg, fg, _) in THEMES.items():
            name = splash_name(width_px, height_px, theme)
            path = IMG_DIR / name
            write_png(path, render_splash(width_px, height_px, bg, fg), width_px, height_px)
            print(f"{name}: {width_px}×{height_px}, {path.stat().st_size} байт")

    write_splash_template(SPLASHES)
    print(f"{SPLASH_TEMPLATE.relative_to(ROOT)}: {len(SPLASHES) * len(THEMES)} ссылок")
