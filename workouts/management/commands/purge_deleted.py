"""Насовсем удалить то, что пролежало дольше срока: корзину и аккаунты.

Запускается ночным scripts/backup.sh после удачного дампа: всё, что удаляется
здесь навсегда, остаётся в свежем бэкапе. Интерфейс от этой чистки не зависит —
показывает и восстанавливает он только последние 30 дней.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts import deletion
from workouts import trash


class Command(BaseCommand):
    help = (
        "Удаляет насовсем тренировки, пролежавшие в корзине дольше 30 дней, и "
        "аккаунты, у которых вышла отсрочка удаления."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="только посчитать, ничего не удалять"
        )

    def handle(self, *args, dry_run=False, **options):
        with transaction.atomic():
            expired = trash.expired()
            count = expired.count()
            if not dry_run:
                expired.delete()
        accounts = deletion.purge_expired(dry_run=dry_run)
        verb = "к удалению" if dry_run else "удалено"
        self.stdout.write(f"корзина: {verb} {count}")
        self.stdout.write(f"аккаунты: {verb} {accounts}")
