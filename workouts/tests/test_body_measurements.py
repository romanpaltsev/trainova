"""Замеры тела: вес, рост, обхваты и свои параметры — журналом с датами."""

import importlib.util
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.utils import timezone

from workouts.models import (
    BodyMeasurement,
    BodyMetric,
    measurement_delta,
    measurement_display,
    parse_measurement_value,
)
from workouts.tests.factories import BodyMeasurementFactory, BodyMetricFactory

pytestmark = pytest.mark.django_db


def load_global_metrics():
    """Список общих параметров из модуля миграции: имя с цифрами не импортируется."""
    path = Path(settings.BASE_DIR) / "workouts" / "migrations" / "0049_global_body_metrics.py"
    spec = importlib.util.spec_from_file_location("global_body_metrics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GLOBAL_METRICS


# ---------- Модель ----------


def test_shipped_global_metrics_match_migration(user):
    """Общие параметры приезжают миграцией — в её порядке, вес первым."""
    shipped = list(BodyMetric.objects.global_only().values_list("name", "unit"))

    assert shipped == load_global_metrics()
    assert BodyMetric.objects.visible_to(user).first().name == "Вес"


def test_own_metrics_come_after_global_ones(user):
    own = BodyMetricFactory(owner=user, name="Шея")

    names = list(BodyMetric.objects.visible_to(user).values_list("name", flat=True))

    assert names[-1] == own.name
    assert names[0] == "Вес"


def test_one_measurement_per_metric_per_day(user):
    metric = BodyMetricFactory()
    today = timezone.localdate()
    BodyMeasurementFactory(user=user, metric=metric, measured_on=today)

    with pytest.raises(IntegrityError), transaction.atomic():
        BodyMeasurementFactory(user=user, metric=metric, measured_on=today)


def test_same_day_is_fine_for_another_user(user, other_user):
    metric = BodyMetricFactory()
    today = timezone.localdate()

    BodyMeasurementFactory(user=user, metric=metric, measured_on=today)
    BodyMeasurementFactory(user=other_user, metric=metric, measured_on=today)

    assert BodyMeasurement.objects.count() == 2


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1")])
def test_database_rejects_non_positive_value(user, value):
    with pytest.raises(IntegrityError), transaction.atomic():
        BodyMeasurementFactory(user=user, value=value)


def test_global_metric_with_measurements_cannot_be_deleted(user):
    """Одним удалением общей записи админ стёр бы всем историю — RESTRICT."""
    metric = BodyMetricFactory(owner=None)
    BodyMeasurementFactory(user=user, metric=metric)

    with pytest.raises(RestrictedError), transaction.atomic():
        metric.delete()


def test_deleting_user_with_own_metric_and_measurements(user):
    """PROTECT сломал бы удаление пользователя: его замеры ссылаются на его же
    параметр. RESTRICT пропускает каскад через пользователя."""
    own = BodyMetricFactory(owner=user)
    BodyMeasurementFactory(user=user, metric=own)
    BodyMeasurementFactory(user=user, metric=BodyMetricFactory(owner=None))

    user.delete()

    assert not BodyMeasurement.objects.exists()
    assert not BodyMetric.objects.filter(pk=own.pk).exists()


# ---------- Разбор и вывод значений ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("82,5", Decimal("82.5")),
        ("82.5", Decimal("82.5")),
        (" 82 ", Decimal("82")),
        ("17,456", Decimal("17.46")),
        ("99999,99", Decimal("99999.99")),
    ],
)
def test_parse_measurement_value(raw, expected):
    assert parse_measurement_value(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "  ", "abc", "0", "0,001", "-1", "nan", "inf", "1e9", "100000"]
)
def test_parse_measurement_value_rejects(raw):
    with pytest.raises(ValueError):
        parse_measurement_value(raw)


def test_measurement_display_keeps_unit_with_number():
    assert measurement_display(Decimal("82.50"), "кг") == "82,5 кг"
    assert measurement_display(Decimal("12000"), "") == "12000"


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [
        (Decimal("82.5"), Decimal("82.1"), "+0,4 кг"),
        (Decimal("82.1"), Decimal("82.5"), "−0,4 кг"),
        (Decimal("82.5"), Decimal("82.50"), "без изменений"),
        (Decimal("82.5"), None, None),
    ],
)
def test_measurement_delta(current, previous, expected):
    assert measurement_delta(current, previous, "кг") == expected


def test_measured_on_defaults_in_factory_are_distinct_days(user):
    first = BodyMeasurementFactory(user=user)
    second = BodyMeasurementFactory(user=user, metric=first.metric)

    assert first.measured_on - second.measured_on == timedelta(days=1)
