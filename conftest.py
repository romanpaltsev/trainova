import pytest

from accounts.tests.factories import PASSWORD, UserFactory


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
