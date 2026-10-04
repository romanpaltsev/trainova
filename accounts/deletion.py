"""Удаление аккаунта с отсрочкой: запрос, отмена входом, чистка через 30 дней.

Запрос отключает аккаунт сразу (is_active=False): ModelBackend.get_user отвергает
неактивных на каждом запросе, поэтому сессии на всех устройствах умирают без
единой строки кода и без лишних запросов. Вход с верным паролем в течение
30 дней аккаунт возвращает (AccountAdapter.pre_login). Удаляет насовсем только
команда purge_deleted — путь удаления один, и он после ночного дампа.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models.deletion import ProtectedError, RestrictedError
from django.utils import timezone

from accounts.models import User
from workouts.models import Workout

GRACE = timedelta(days=30)

logger = logging.getLogger(__name__)


def deadline(user):
    """Когда аккаунт удалится насовсем."""
    return user.deletion_requested_at + GRACE


def is_restorable(user, now=None):
    """Удаление запрошено, но срок ещё не вышел — вход вернёт аккаунт."""
    if user.deletion_requested_at is None:
        return False
    return (now or timezone.now()) < deadline(user)


def request(user):
    """Запросить удаление: аккаунт отключается сразу, данные живут 30 дней."""
    user.deletion_requested_at = timezone.now()
    user.is_active = False
    user.save(update_fields=["deletion_requested_at", "is_active"])


def cancel(user):
    """Передумал: аккаунт снова активен, отметка о запросе стёрта."""
    user.deletion_requested_at = None
    user.is_active = True
    user.save(update_fields=["deletion_requested_at", "is_active"])


def delete_user_data(user):
    """Удалить пользователя со всеми данными — в два шага.

    Сначала тренировки, потом сам пользователь. Одним user.delete() нельзя:
    подход держит упражнение через PROTECT, и своё упражнение (вид спорта,
    место), использованное в своих же тренировках, уронило бы весь каскад —
    PROTECT не делает исключения для строк, удаляемых тем же каскадом.
    """
    with transaction.atomic():
        Workout.objects.filter(user=user).delete()
        user.delete()


def expired(now=None):
    """Аккаунты, у которых срок отсрочки вышел."""
    return User.objects.filter(deletion_requested_at__lte=(now or timezone.now()) - GRACE)


def purge_expired(dry_run=False):
    """Удалить насовсем аккаунты после отсрочки. Возвращает число удалённых.

    Каждый — в своей точке отката: один сбойный не держит остальных.
    """
    users = list(expired())
    if dry_run:
        return len(users)
    deleted = 0
    for user in users:
        try:
            with transaction.atomic():
                delete_user_data(user)
            deleted += 1
        except (ProtectedError, RestrictedError):
            logger.exception("не удалось удалить аккаунт %s", user.pk)
    return deleted
