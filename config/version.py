"""Версия продукта: «дата · коммит», например «2026.10.09 · db88365».

Номер руками не поднимается — его считает деплой. scripts/prod.sh перед каждой
командой пишет файл VERSION (в .gitignore), он едет в образ вместе с кодом: .git
туда не попадает. В разработке файла нет, и версия берётся из git напрямую.
Без обоих — «dev». version в pyproject.toml — поле для uv, а не версия продукта.
"""

import os
import subprocess
from pathlib import Path

# Формат один на деплой и разработку: scripts/prod.sh собирает ту же строку из git.
GIT_FORMAT = ["log", "-1", "--date=format-local:%Y.%m.%d", "--format=%cd · %h"]


def read_app_version(base_dir: Path) -> str:
    try:
        version = (base_dir / "VERSION").read_text("utf-8").strip()
    except OSError:
        version = ""
    if version:
        return version
    try:
        result = subprocess.run(
            ["git", *GIT_FORMAT],
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "TZ": "Europe/Moscow"},
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "dev"
    return result.stdout.strip() or "dev"
