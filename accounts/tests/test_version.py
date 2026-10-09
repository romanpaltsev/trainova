"""Версия продукта «дата · коммит» (config/version.py): файл деплоя важнее git."""

from config.version import read_app_version


def test_version_file_written_by_deploy_wins(tmp_path):
    (tmp_path / "VERSION").write_text("2026.10.09 · db88365\n", "utf-8")

    assert read_app_version(tmp_path) == "2026.10.09 · db88365"


def test_without_file_and_git_version_is_dev(tmp_path):
    """Каталог без VERSION и вне репозитория — «dev», а не падение настроек."""
    assert read_app_version(tmp_path) == "dev"
