"""Удаление аккаунта с отсрочкой 30 дней.

Запрос отключает аккаунт сразу (is_active=False) — на всех устройствах. Вход с
верным паролем до срока его возвращает, после срока — «аккаунт недоступен».
Насовсем удаляет только команда purge_deleted, и она обязана пройти мимо
PROTECT: свои упражнения, места и виды спорта держатся своими же тренировками.
"""

from datetime import timedelta

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import deletion
from accounts.models import User
from accounts.tests.factories import PASSWORD, UserFactory
from workouts.models import BodyMeasurement, BodyMetric, Exercise, Location, Sport, Workout
from workouts.tests.factories import (
    BodyMeasurementFactory,
    BodyMetricFactory,
    ExerciseFactory,
    LocationFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db

URL = "/profile/delete/"


@pytest.fixture(autouse=True)
def clean_rate_limits():
    """Лимиты попыток входа живут в кеше процесса — между тестами их не тащим."""
    cache.clear()
    yield
    cache.clear()


def request_deletion(client, password=PASSWORD):
    return client.post(reverse("account_deletion"), {"password": password})


def log_in(client, user, password=PASSWORD):
    return client.post(reverse("account_login"), {"login": user.email, "password": password})


def test_url_is_stable():
    assert reverse("account_deletion") == URL


def test_page_requires_login(client):
    response = client.get(reverse("account_deletion"))

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_page_offers_export_and_names_the_deadline(client, user):
    client.force_login(user)

    content = client.get(reverse("account_deletion")).content.decode()

    assert reverse("workout_export") in content
    assert reverse("exercise_export") in content
    deadline = timezone.localdate() + timedelta(days=30)
    assert f"<strong>{deadline.day} " in content
    assert 'name="password"' in content


def test_profile_links_to_deletion(client, user):
    client.force_login(user)

    assert reverse("account_deletion") in client.get(reverse("profile")).content.decode()


def test_wrong_password_changes_nothing(client, user):
    client.force_login(user)

    response = request_deletion(client, password="не тот")

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.is_active and user.deletion_requested_at is None
    assert not mail.outbox


def test_correct_password_switches_off_and_logs_out(client, user):
    client.force_login(user)

    response = request_deletion(client)

    assert response.url == reverse("account_login")
    user.refresh_from_db()
    assert not user.is_active
    assert user.deletion_requested_at is not None
    assert "_auth_user_id" not in client.session
    page = client.get(response.url).content.decode()
    assert "Аккаунт будет удалён" in page
    assert len(mail.outbox) == 1
    assert "будет удалён" in mail.outbox[0].subject
    assert user.email in mail.outbox[0].to


def test_other_devices_are_logged_out_too(client, user):
    """is_active=False: ModelBackend отвергает неактивного на каждом запросе."""
    phone = Client()
    phone.force_login(user)
    client.force_login(user)

    request_deletion(client)

    response = phone.get(reverse("dashboard"))
    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_login_within_grace_brings_account_back(client, user):
    client.force_login(user)
    request_deletion(client)

    response = log_in(client, user)

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.is_active and user.deletion_requested_at is None
    page = client.get(reverse("dashboard")).content.decode()
    assert "Удаление аккаунта отменено" in page


def test_login_after_grace_is_refused(client, user):
    client.force_login(user)
    request_deletion(client)
    User.objects.filter(pk=user.pk).update(
        deletion_requested_at=timezone.now() - timedelta(days=31)
    )

    response = log_in(client, user)

    assert response.url == reverse("account_inactive")
    user.refresh_from_db()
    assert not user.is_active


def test_wrong_password_while_pending_does_not_restore(client, user):
    client.force_login(user)
    request_deletion(client)

    response = log_in(client, user, password="не тот")

    assert response.status_code == 200
    user.refresh_from_db()
    assert not user.is_active


def test_password_guessing_is_rate_limited(client, user):
    client.force_login(user)

    for _ in range(5):
        request_deletion(client, password="не тот")
    content = request_deletion(client, password="не тот").content.decode()

    assert "Слишком много" in content
    user.refresh_from_db()
    assert user.is_active


def test_staff_cannot_delete_themselves(client):
    admin = UserFactory(email="admin@example.com", is_staff=True)
    client.force_login(admin)

    assert reverse("account_deletion") not in client.get(reverse("profile")).content.decode()
    response = request_deletion(client)

    assert response.url == reverse("profile")
    admin.refresh_from_db()
    assert admin.is_active and admin.deletion_requested_at is None


def test_password_reset_reaches_pending_account(client, user):
    """Забыл пароль во время отсрочки — письмо со ссылкой, а не «аккаунта нет»."""
    client.force_login(user)
    request_deletion(client)
    mail.outbox.clear()

    client.post(reverse("account_reset_password"), {"email": user.email})

    assert len(mail.outbox) == 1
    assert "/accounts/password/reset/key/" in mail.outbox[0].body


def test_pending_account_cannot_be_active_in_the_database(user):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(deletion_requested_at=timezone.now())


def test_request_acts_only_on_the_signed_in_user(client, user, other_user):
    client.force_login(user)

    request_deletion(client)

    other_user.refresh_from_db()
    assert other_user.is_active and other_user.deletion_requested_at is None


# ---------- Чистка ----------


def pending(user, days_ago):
    User.objects.filter(pk=user.pk).update(
        is_active=False, deletion_requested_at=timezone.now() - timedelta(days=days_ago)
    )


def test_purge_deletes_user_held_by_own_catalogs(user, other_user):
    """Своё упражнение в своих подходах, свой вид спорта и место в своих
    тренировках, свой параметр с замерами — одним user.delete() это упёрлось бы
    в PROTECT. Общие справочники и чужие данные остаются."""
    exercise = ExerciseFactory(owner=user, name="Моё упражнение")
    sport = SportFactory(owner=user, name="Мой вид спорта")
    place = LocationFactory(owner=user, name="Мой зал")
    common = ExerciseFactory(name="Общее упражнение")
    workout = WorkoutFactory(user=user, sport=sport, location=place)
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1)
    StrengthSetFactory(workout=workout, exercise=common, set_number=1)
    metric = BodyMetricFactory(owner=user)
    BodyMeasurementFactory(user=user, metric=metric)
    foreign = WorkoutFactory(user=other_user)
    pending(user, days_ago=31)

    call_command("purge_deleted")

    assert not User.objects.filter(pk=user.pk).exists()
    assert not EmailAddress.objects.filter(email=user.email).exists()
    assert not Workout.objects.filter(pk=workout.pk).exists()
    assert not Exercise.objects.filter(pk=exercise.pk).exists()
    assert not Sport.objects.filter(pk=sport.pk).exists()
    assert not Location.objects.filter(pk=place.pk).exists()
    assert not BodyMetric.objects.filter(pk=metric.pk).exists()
    assert not BodyMeasurement.objects.filter(user_id=user.pk).exists()
    assert Exercise.objects.filter(pk=common.pk).exists()
    assert Workout.objects.filter(pk=foreign.pk).exists()


def test_purge_keeps_fresh_and_admin_disabled_accounts(user, other_user):
    pending(user, days_ago=29)
    User.objects.filter(pk=other_user.pk).update(is_active=False)

    call_command("purge_deleted")

    assert User.objects.filter(pk__in=[user.pk, other_user.pk]).count() == 2


def test_purge_dry_run_deletes_nothing(user):
    pending(user, days_ago=40)

    call_command("purge_deleted", "--dry-run")

    assert User.objects.filter(pk=user.pk).exists()


def test_restorable_window_edges(user):
    user.deletion_requested_at = timezone.now() - timedelta(days=29, hours=23)
    assert deletion.is_restorable(user)
    user.deletion_requested_at = timezone.now() - timedelta(days=30, minutes=1)
    assert not deletion.is_restorable(user)
