import pytest

from accounts.tests.factories import PASSWORD, UserFactory


@pytest.fixture(autouse=True)
def fast_password_hasher(settings):
    """Быстрый хешер паролей вместо прод-PBKDF2.

    Полтора миллиона итераций PBKDF2 — защита боевой базы, а в тестах это 0,2 с на
    каждого UserFactory и каждый вход формой: две трети времени всего набора.
    """
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def user(db):
    """Пользователь с подтверждённым email — готов к входу."""
    from allauth.account.models import EmailAddress

    user = UserFactory()
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return user


@pytest.fixture
def other_user(db):
    """Второй пользователь — для проверок изоляции данных."""
    return UserFactory()


@pytest.fixture
def password():
    return PASSWORD
