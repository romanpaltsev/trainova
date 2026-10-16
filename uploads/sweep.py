"""Уборка файлов, на которые больше не ссылается ни одна строка.

Путь удаления файлов один — этот: замена фото, удаление записи, каскад
удаления аккаунта и откат транзакции после записи файла оставляют на диске
сироту, и всех их убирает ночная purge_deleted (её зовёт scripts/backup.sh
после выгрузки бэкапа — удалённое остаётся в облаке). Сигналов post_delete
нет намеренно: каждый путь пришлось бы помнить, а сирота после падения всё
равно осталась бы.
"""

from datetime import timedelta
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import models
from django.utils import timezone

# Свежий файл не трогаем, даже если ссылки нет: его транзакция могла ещё не
# закончиться. Сутки — с большим запасом против любого запроса.
GRACE = timedelta(days=1)


def referenced_names():
    """Имена всех файлов, записанных в FileField любой модели: реестра нет —
    новое поле с файлами уборка узнаёт сама."""
    names = set()
    for model in apps.get_models():
        fields = [
            field.attname
            for field in model._meta.concrete_fields
            if isinstance(field, models.FileField)
        ]
        if fields:
            for row in model._base_manager.values_list(*fields):
                names.update(name for name in row if name)
    return names


def stored_files():
    """[(имя в хранилище, путь)] всех файлов MEDIA_ROOT; скрытые пропускаются."""
    root = Path(settings.MEDIA_ROOT)
    if not root.is_dir():
        return []
    return [
        (path.relative_to(root).as_posix(), path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.startswith(".")
    ]


def orphans(now=None):
    """Пути файлов без ссылки из базы, пролежавших дольше GRACE."""
    cutoff = ((now or timezone.now()) - GRACE).timestamp()
    referenced = referenced_names()
    return [
        path
        for name, path in stored_files()
        if name not in referenced and path.stat().st_mtime < cutoff
    ]


def sweep(now=None):
    """Удалить сирот; вернуть их число."""
    paths = orphans(now)
    for path in paths:
        path.unlink(missing_ok=True)
    return len(paths)


def usage():
    """(файлов, байт) в хранилище — для «Состояния системы»."""
    files = stored_files()
    return len(files), sum(path.stat().st_size for _name, path in files)
