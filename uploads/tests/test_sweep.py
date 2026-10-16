"""Ночная уборка файлов без записи и её место в бэкапе."""

import os
import time
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

from uploads import sweep

DAY = 24 * 60 * 60


def put(root, name, *, age=0, content=b"jpeg"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    moment = time.time() - age
    os.utime(path, (moment, moment))
    return path


@pytest.fixture
def unreferenced(monkeypatch):
    """Ни одна строка ни на что не ссылается — проверяем саму уборку."""
    monkeypatch.setattr(sweep, "referenced_names", set)


@pytest.mark.django_db
def test_sweep_removes_old_orphans_and_keeps_the_rest(media_root, monkeypatch):
    old = put(media_root, "machines/old.jpg", age=2 * DAY)
    kept = put(media_root, "machines/kept.jpg", age=2 * DAY)
    fresh = put(media_root, "machines/fresh.jpg", age=60)
    hidden = put(media_root, ".keep", age=2 * DAY)
    monkeypatch.setattr(sweep, "referenced_names", lambda: {"machines/kept.jpg"})

    assert sweep.sweep() == 1

    assert not old.exists()
    assert kept.exists(), "на файл ссылается запись"
    assert fresh.exists(), "транзакция загрузки могла ещё не закончиться"
    assert hidden.exists()


@pytest.mark.django_db
def test_missing_media_root_is_not_an_error(media_root):
    assert not media_root.exists()

    assert sweep.sweep() == 0
    assert sweep.usage() == (0, 0)


def test_usage_counts_files_and_bytes(media_root):
    put(media_root, "machines/a.jpg", content=b"12345")
    put(media_root, "machines/b.jpg", content=b"123")

    assert sweep.usage() == (2, 8)


@pytest.mark.django_db
def test_purge_deleted_sweeps_files(media_root, unreferenced, capsys):
    orphan = put(media_root, "machines/orphan.jpg", age=2 * DAY)

    call_command("purge_deleted", "--dry-run")
    assert orphan.exists()
    assert "файлы: к удалению 1" in capsys.readouterr().out

    call_command("purge_deleted")
    assert not orphan.exists()
    assert "файлы: удалено 1" in capsys.readouterr().out


def test_backup_uploads_photos_after_dump_and_before_purge():
    """Фото уезжают в облако до уборки: файл, который она сотрёт этой ночью,
    уже лежит в бэкапе. И только добавлением — copy, а не sync."""
    script = (Path(settings.BASE_DIR) / "scripts" / "backup.sh").read_text()

    upload = script.index('rclone copy "$MEDIA_DIR"')
    assert script.index("pg_restore --list") < upload < script.index("manage.py purge_deleted")
    assert "rclone sync" not in script


def test_restore_brings_photos_back_without_deleting():
    script = (Path(settings.BASE_DIR) / "scripts" / "restore.sh").read_text()

    assert 'rclone copy "${REMOTE}/media" "$MEDIA_DIR"' in script
    assert "rclone sync" not in script
