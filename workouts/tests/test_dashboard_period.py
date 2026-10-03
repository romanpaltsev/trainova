"""Период сводки дашборда: готовые окна, свой период, сравнение с прошлым окном.

Период меняет только сводку — заголовок и плитки. Выбор живёт в адресе:
?period=30 для готовых окон и ?from=…&to=… для своего; мусор в адресе даёт
7 дней, как неизвестный фильтр истории.
"""

from datetime import date, datetime, timedelta

import pytest
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from workouts import stats
from workouts.tests.factories import WorkoutFactory
from workouts.views import parse_period

TODAY = date(2026, 10, 3)


def workout_on(user, day, **kwargs):
    started = timezone.make_aware(datetime(day.year, day.month, day.day, 12))
    return WorkoutFactory(user=user, started_at=started, **kwargs)


def period(query):
    return parse_period(QueryDict(query), TODAY)


# ---------- Адрес → окно ----------


@pytest.mark.parametrize(
    ("query", "key", "days"),
    [
        ("", "7", 7),
        ("period=30", "30", 30),
        ("period=90", "90", 90),
        ("period=365", "365", 365),
        ("period=мусор", "7", 7),
    ],
)
def test_preset_windows_end_today(query, key, days):
    window = period(query)

    assert (window.key, window.end) == (key, TODAY)
    assert window.start == TODAY - timedelta(days=days - 1)


def test_all_time_has_no_start_and_nothing_to_compare_with():
    window = period("period=all")

    assert (window.key, window.start, window.title) == ("all", None, "За всё время")
    assert window.compare_label == ""


def test_custom_period_from_two_dates():
    window = period("from=2026-09-01&to=2026-09-30")

    assert (window.key, window.start, window.end) == ("custom", date(2026, 9, 1), date(2026, 9, 30))
    assert (window.title, window.compare_label) == ("За 30 дней", "к прошлым 30 дням")


def test_custom_period_wins_over_preset():
    assert period("period=all&from=2026-09-01&to=2026-09-30").key == "custom"


def test_swapped_dates_are_put_back_in_order():
    window = period("from=2026-09-30&to=2026-09-01")

    assert (window.start, window.end) == (date(2026, 9, 1), date(2026, 9, 30))


def test_future_end_is_cut_to_today():
    """Будущих тренировок не бывает: окно до сегодня, а не пустой хвост."""
    assert period("from=2026-09-01&to=2027-01-01").end == TODAY


def test_ancient_start_is_raised_to_earliest_day():
    """С «0001-01-01» прошлое окно ушло бы за начало календаря — 500 вместо сводки."""
    assert period("from=0001-01-01&to=2026-10-03").start == stats.EARLIEST_DAY


@pytest.mark.parametrize(
    "query",
    [
        "from=2026-02-30&to=2026-03-01",  # такого дня нет
        "from=2027-01-01&to=2027-02-01",  # целиком в будущем
        "from=2026-09-01",  # одна дата — не период
        "from=abc&to=def",
    ],
)
def test_impossible_custom_period_falls_back_to_seven_days(query):
    assert period(query).key == "7"


@pytest.mark.parametrize(
    ("days", "title", "compare_label"),
    [
        (1, "За 1 день", "к прошлому дню"),
        (2, "За 2 дня", "к прошлым 2 дням"),
        (12, "За 12 дней", "к прошлым 12 дням"),
        (21, "За 21 день", "к прошлым 21 дню"),
    ],
)
def test_custom_period_labels_agree_with_number(days, title, compare_label):
    window = stats.custom_period(TODAY - timedelta(days=days - 1), TODAY)

    assert (window.title, window.compare_label) == (title, compare_label)


@pytest.mark.parametrize(
    ("start", "end", "text"),
    [
        (date(2026, 9, 27), TODAY, "27 сен — 3 окт"),
        (date(2026, 9, 1), date(2026, 9, 30), "1 — 30 сен"),
        (TODAY, TODAY, "3 окт"),
        (date(2025, 10, 4), TODAY, "4 окт 2025 — 3 окт"),
    ],
)
def test_range_display_writes_year_only_for_other_years(start, end, text):
    assert stats.range_display(start, end, TODAY) == text


# ---------- Сводка окна ----------


@pytest.mark.django_db
def test_thirty_days_count_their_window_and_compare_with_previous_thirty(user):
    workout_on(user, TODAY - timedelta(days=20))
    workout_on(user, TODAY - timedelta(days=29))  # первый день окна — внутри
    workout_on(user, TODAY - timedelta(days=30))  # прошлое окно
    workout_on(user, TODAY - timedelta(days=60))  # за пределами обоих

    summary = stats.period_summary(user, stats.preset_period("30", TODAY), TODAY)

    assert summary["count"] == 2
    assert summary["count_delta_label"]["label"] == "+1 к прошлым 30 дням"
    assert summary["days_label"] == "2 дня из 30"
    assert summary["badges"]["count"]["label"] == "+1"


@pytest.mark.django_db
def test_custom_period_bounds_are_inclusive(user):
    workout_on(user, date(2026, 9, 1))
    workout_on(user, date(2026, 9, 30))
    workout_on(user, date(2026, 10, 1))

    window = stats.custom_period(date(2026, 9, 1), date(2026, 9, 30))
    summary = stats.period_summary(user, window, TODAY)

    assert summary["count"] == 2
    assert summary["range_display"] == "1 — 30 сен"


@pytest.mark.django_db
def test_all_time_counts_everything_finished_without_comparison(user):
    first = date(2025, 3, 3)
    workout_on(user, first)
    workout_on(user, TODAY - timedelta(days=1))
    # Идущая и подготовленная не записаны — ни в каком окне их нет.
    workout_on(user, TODAY, duration_min=None)
    WorkoutFactory(user=user, started_at=None, duration_min=None)

    summary = stats.period_summary(user, stats.preset_period("all", TODAY), TODAY)

    assert summary["count"] == 2
    assert summary["start"] == first
    assert summary["range_display"] == "3 мар 2025 — 3 окт"
    assert summary["days_label"] == f"2 дня из {(TODAY - first).days + 1}"
    assert summary["compare"] is False
    assert summary["count_delta_label"] is None
    assert all(badge is None for badge in summary["badges"].values())


@pytest.mark.django_db
def test_all_time_without_workouts_starts_today(user):
    summary = stats.period_summary(user, stats.preset_period("all", TODAY), TODAY)

    assert (summary["start"], summary["count"], summary["days_label"]) == (TODAY, 0, "")


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["30", "all"])
def test_period_summary_ignores_other_users(user, other_user, key):
    workout_on(other_user, TODAY - timedelta(days=3))

    summary = stats.period_summary(user, stats.preset_period(key, TODAY), TODAY)

    assert summary["count"] == 0


# ---------- Страница ----------


def page(client, query=""):
    return client.get(f"{reverse('dashboard')}?{query}").content.decode()


@pytest.mark.django_db
def test_dashboard_opens_with_seven_days_and_clean_address(client, user):
    client.force_login(user)

    content = page(client)

    assert '<h1 class="app-page-title mb-0">За 7 дней</h1>' in content
    dashboard = reverse("dashboard")
    active = f'class="app-chip is-active" href="{dashboard}" aria-current="true">7 дней</a>'
    assert active in content
    assert f'href="{dashboard}?period=30"' in content


@pytest.mark.django_db
def test_chip_switches_title_and_highlight(client, user):
    client.force_login(user)

    content = page(client, "period=90")

    assert '<h1 class="app-page-title mb-0">За 3 месяца</h1>' in content
    assert 'aria-current="true">3 месяца</a>' in content


@pytest.mark.django_db
def test_badge_hint_follows_period(client, user):
    WorkoutFactory(user=user, started_at=timezone.now() - timedelta(days=2))
    client.force_login(user)

    content = page(client, "period=30")

    assert 'title="к прошлым 30 дням"' in content
    assert "к прошлым 7 дням" not in content


@pytest.mark.django_db
def test_all_time_page_has_no_comparison(client, user):
    WorkoutFactory(user=user, started_at=timezone.now() - timedelta(days=40))
    client.force_login(user)

    content = page(client, "period=all")

    assert "За всё время" in content
    assert "к прошлым" not in content
    assert "app-stat-badge" not in content


@pytest.mark.django_db
def test_custom_chip_opens_modal_with_dates_of_open_summary(client, user):
    today = timezone.localdate()
    first, last = today - timedelta(days=20), today - timedelta(days=10)
    client.force_login(user)

    content = page(client, f"from={first:%Y-%m-%d}&to={last:%Y-%m-%d}")

    modal = f"{reverse('dashboard_period')}?from={first:%Y-%m-%d}&amp;to={last:%Y-%m-%d}"
    assert f'hx-get="{modal}"' in content
    assert "За 11 дней" in content


@pytest.mark.django_db
def test_page_counts_only_own_workouts_in_any_period(client, user, other_user):
    WorkoutFactory(user=other_user, started_at=timezone.now() - timedelta(days=3))
    client.force_login(user)

    for query in ("period=all", "period=30"):
        response = client.get(f"{reverse('dashboard')}?{query}")
        assert response.context["summary"]["count"] == 0


# ---------- Окно «Свой период» ----------


@pytest.mark.django_db
def test_period_modal_requires_login(client):
    response = client.get(reverse("dashboard_period"))

    assert response.status_code == 302
    assert reverse("account_login") in response.url


@pytest.mark.django_db
def test_period_modal_prefills_dates_and_submits_to_dashboard(client, user):
    client.force_login(user)

    content = client.get(
        reverse("dashboard_period"), {"from": "2026-09-01", "to": "2026-09-30"}
    ).content.decode()

    assert f'<form method="get" action="{reverse("dashboard")}">' in content
    assert 'value="2026-09-01"' in content
    assert 'value="2026-09-30"' in content
    assert f'max="{timezone.localdate():%Y-%m-%d}"' in content


@pytest.mark.django_db
def test_period_modal_defaults_to_last_seven_days(client, user):
    today = timezone.localdate()
    client.force_login(user)

    content = client.get(reverse("dashboard_period")).content.decode()

    assert f'value="{today - timedelta(days=6):%Y-%m-%d}"' in content
    assert f'value="{today:%Y-%m-%d}"' in content
