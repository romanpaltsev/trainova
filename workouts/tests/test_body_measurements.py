"""Замеры тела: вес, рост, обхваты и свои параметры — журналом с датами."""

import importlib.util
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.urls import reverse
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


# ---------- Страницы и модалки ----------


def weight():
    return BodyMetric.objects.get(owner=None, name="Вес")


def add(client, metric, value, day=None, back="list"):
    return client.post(
        reverse("body_measurement_add"),
        {
            "metric": metric.pk,
            "value": value,
            "measured_on": (day or timezone.localdate()).isoformat(),
            "back": back,
        },
    )


def test_list_requires_login(client):
    response = client.get(reverse("body_measurements"))

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_list_shows_last_value_and_change(client, user):
    today = timezone.localdate()
    BodyMeasurementFactory(
        user=user, metric=weight(), value=Decimal("82.9"), measured_on=today - timedelta(days=7)
    )
    BodyMeasurementFactory(user=user, metric=weight(), value=Decimal("82.5"), measured_on=today)

    client.force_login(user)
    response = client.get(reverse("body_measurements"))

    content = response.content.decode()
    assert "82,5 кг" in content
    assert "−0,4 кг" in content
    assert [m.name for m in response.context["tracked"]] == ["Вес"]
    assert "Рост" in [m.name for m in response.context["untracked"]]


def test_values_are_isolated_between_users(client, user, other_user):
    """Священное правило: «Вес» общий, а значения у каждого свои — в списке,
    на странице параметра и в данных графика."""
    BodyMeasurementFactory(user=other_user, metric=weight(), value=Decimal("123.4"))
    BodyMeasurementFactory(user=user, metric=weight(), value=Decimal("70"))

    client.force_login(user)
    listing = client.get(reverse("body_measurements")).content.decode()
    page = client.get(reverse("body_metric", args=[weight().pk]))

    assert "123,4" not in listing
    assert "70 кг" in listing
    assert page.context["chart"]["values"] == [70.0]
    assert "123,4" not in page.content.decode()


def test_metric_page_chart_values_are_floats(client, user):
    BodyMeasurementFactory(user=user, metric=weight(), value=Decimal("82.50"))
    BodyMeasurementFactory(user=user, metric=weight(), value=Decimal("83.10"))

    client.force_login(user)
    chart = client.get(reverse("body_metric", args=[weight().pk])).context["chart"]

    assert chart["values"] == [83.1, 82.5]  # по возрастанию дня
    assert all(isinstance(value, float) for value in chart["values"])


def test_other_users_own_metric_is_404(client, user, other_user):
    alien = BodyMetricFactory(owner=other_user, name="Чужой параметр")
    entry = BodyMeasurementFactory(user=other_user, metric=alien)

    client.force_login(user)

    for url in (
        reverse("body_metric", args=[alien.pk]),
        reverse("body_metric_rename", args=[alien.pk]),
        reverse("body_metric_delete", args=[alien.pk]),
        reverse("body_measurement_edit", args=[entry.pk]),
    ):
        assert client.get(url).status_code == 404
    assert client.post(reverse("body_metric_delete", args=[alien.pk])).status_code == 404
    assert (
        client.post(reverse("body_measurement_edit", args=[entry.pk]), {"delete": "1"}).status_code
        == 404
    )
    assert BodyMeasurement.objects.filter(pk=entry.pk).exists()


def test_global_metric_cannot_be_renamed_or_deleted(client, user):
    client.force_login(user)

    assert client.get(reverse("body_metric_rename", args=[weight().pk])).status_code == 404
    assert client.post(reverse("body_metric_delete", args=[weight().pk])).status_code == 404


def test_adding_measurement_redirects_back(client, user):
    client.force_login(user)

    to_list = add(client, weight(), "82,5")
    to_metric = add(
        client, weight(), "82,4", day=timezone.localdate() - timedelta(days=1), back="metric"
    )
    to_garbage = add(
        client,
        weight(),
        "82,3",
        day=timezone.localdate() - timedelta(days=2),
        back="https://evil.example",
    )

    assert to_list["HX-Redirect"] == reverse("body_measurements")
    assert to_metric["HX-Redirect"] == reverse("body_metric", args=[weight().pk])
    assert to_garbage["HX-Redirect"] == reverse("body_measurements")
    assert BodyMeasurement.objects.filter(user=user).count() == 3


def test_same_day_entry_overwrites_value(client, user):
    """Повторный ввод за тот же день — исправление, а не второй замер."""
    client.force_login(user)

    add(client, weight(), "82,5")
    response = add(client, weight(), "82,1")

    entry = BodyMeasurement.objects.get(user=user)
    assert entry.value == Decimal("82.1")
    assert "обновлён" in str(list(get_messages(response.wsgi_request))[-1])


@pytest.mark.parametrize(
    ("value", "day_offset", "error"),
    [
        ("82,5", 1, "Дата не может быть в будущем."),
        ("0", 0, "Значение должно быть больше нуля."),
        ("abc", 0, "Значение — это число"),
    ],
)
def test_invalid_measurement_keeps_modal(client, user, value, day_offset, error):
    client.force_login(user)

    response = add(client, weight(), value, day=timezone.localdate() + timedelta(days=day_offset))

    assert "HX-Redirect" not in response
    assert error in response.content.decode()
    assert not BodyMeasurement.objects.exists()


def test_other_users_own_metric_cannot_be_measured(client, user, other_user):
    alien = BodyMetricFactory(owner=other_user, name="Чужой параметр")
    client.force_login(user)

    response = add(client, alien, "40")

    assert "Такого параметра у вас нет." in response.content.decode()
    assert not BodyMeasurement.objects.exists()


def test_editing_measurement_value_and_day(client, user):
    entry = BodyMeasurementFactory(user=user, metric=weight(), value=Decimal("82.5"))
    new_day = entry.measured_on - timedelta(days=10)

    client.force_login(user)
    response = client.post(
        reverse("body_measurement_edit", args=[entry.pk]),
        {"value": "81,9", "measured_on": new_day.isoformat(), "back": "metric"},
    )

    entry.refresh_from_db()
    assert (entry.value, entry.measured_on) == (Decimal("81.9"), new_day)
    assert response["HX-Redirect"] == reverse("body_metric", args=[weight().pk])


def test_moving_measurement_to_taken_day_is_refused(client, user):
    today = timezone.localdate()
    BodyMeasurementFactory(user=user, metric=weight(), measured_on=today)
    entry = BodyMeasurementFactory(
        user=user, metric=weight(), measured_on=today - timedelta(days=1)
    )

    client.force_login(user)
    response = client.post(
        reverse("body_measurement_edit", args=[entry.pk]),
        {"value": "80", "measured_on": today.isoformat(), "back": "metric"},
    )

    assert "замер уже есть" in response.content.decode()
    entry.refresh_from_db()
    assert entry.measured_on == today - timedelta(days=1)


def test_deleting_measurement(client, user):
    entry = BodyMeasurementFactory(user=user, metric=weight())

    client.force_login(user)
    client.post(
        reverse("body_measurement_edit", args=[entry.pk]), {"delete": "1", "back": "metric"}
    )

    assert not BodyMeasurement.objects.filter(pk=entry.pk).exists()


def test_creating_and_renaming_own_metric(client, user):
    client.force_login(user)

    response = client.post(reverse("body_metric_create"), {"name": " Шея ", "unit": "см"})
    own = BodyMetric.objects.get(owner=user)
    client.post(reverse("body_metric_rename", args=[own.pk]), {"name": "Обхват шеи", "unit": "мм"})

    own.refresh_from_db()
    assert response["HX-Redirect"] == reverse("body_metric", args=[own.pk])
    assert (own.name, own.unit) == ("Обхват шеи", "мм")


def test_own_metric_cannot_duplicate_global_name(client, user):
    client.force_login(user)

    response = client.post(reverse("body_metric_create"), {"name": "вес", "unit": "кг"})

    assert "Такой параметр у вас уже есть." in response.content.decode()
    assert not BodyMetric.objects.filter(owner=user).exists()


def test_deleting_own_metric_removes_its_measurements(client, user):
    own = BodyMetricFactory(owner=user, name="Шея")
    BodyMeasurementFactory(user=user, metric=own)
    BodyMeasurementFactory(user=user, metric=own)
    kept = BodyMeasurementFactory(user=user, metric=weight())

    client.force_login(user)
    confirm = client.get(reverse("body_metric_delete", args=[own.pk])).content.decode()
    response = client.post(reverse("body_metric_delete", args=[own.pk]))

    assert "2 замера" in confirm
    assert "удалятся все его замеры" in confirm
    assert response.url == reverse("body_measurements")
    assert not BodyMetric.objects.filter(pk=own.pk).exists()
    assert list(BodyMeasurement.objects.filter(user=user)) == [kept]


@pytest.mark.parametrize("scale", [1, 10])
def test_list_query_budget(client, user, django_assert_max_num_queries, scale):
    """Последнее и предыдущее значения — подзапросами одного запроса: число
    параметров и замеров на число запросов не влияет."""
    metrics = [BodyMetricFactory(owner=user) for _ in range(scale)]
    for metric in metrics:
        for _ in range(2 * scale):
            BodyMeasurementFactory(user=user, metric=metric)

    client.force_login(user)
    with django_assert_max_num_queries(5):
        client.get(reverse("body_measurements"))


@pytest.mark.parametrize("count", [2, 50])
def test_metric_page_query_budget(client, user, django_assert_max_num_queries, count):
    metric = weight()
    for _ in range(count):
        BodyMeasurementFactory(user=user, metric=metric)
    url = reverse("body_metric", args=[metric.pk])

    client.force_login(user)
    with django_assert_max_num_queries(6):
        client.get(url)
