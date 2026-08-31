"""Панель мастер-детали справочника: та же вьюха отдаёт тело вместо страницы.

На десктопе упражнение открывается справа от списка HTMX-запросом, на мобильном —
отдельной страницей. Дверь одна (`exercise_detail`), и она обязана быть закрыта
одинаково для обоих случаев: тесты ниже проверяют партиальную ветку отдельно,
чтобы будущий рефакторинг не снял защиту тихо.
"""

import pytest
from django.urls import reverse

from workouts.tests.factories import ExerciseFactory, StrengthSetFactory, WorkoutFactory

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}


def panel(client, exercise):
    return client.get(reverse("exercise_detail", args=[exercise.pk]), headers=HX)


def test_panel_returns_body_without_base(client, user):
    exercise = ExerciseFactory(name="Жим лёжа", owner=None)
    workout = WorkoutFactory(user=user)
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, weight_kg=80, reps=8)

    client.force_login(user)
    content = panel(client, exercise).content.decode()

    assert "История подходов" in content
    # Базы вокруг нет: ни доктайпа, ни навигации — это фрагмент для панели.
    assert "<!doctype" not in content.lower()
    assert "app-bottom-nav" not in content


def test_normal_request_still_returns_full_page(client, user):
    """Страж мобильного поведения: без заголовка HTMX это по-прежнему страница."""
    exercise = ExerciseFactory(name="Жим лёжа", owner=None)

    client.force_login(user)
    content = client.get(reverse("exercise_detail", args=[exercise.pk])).content.decode()

    assert "app-bottom-nav" in content
    assert "История подходов" in content


def test_panel_404_for_foreign_personal_exercise(client, user, other_user):
    """Священное правило на новой двери: чужое личное упражнение недоступно."""
    alien = ExerciseFactory(name="Чужое упражнение", owner=other_user)

    client.force_login(user)

    assert panel(client, alien).status_code == 404


def test_panel_requires_login(client, user):
    exercise = ExerciseFactory(name="Жим лёжа", owner=None)

    response = panel(client, exercise)

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_panel_of_global_exercise_shows_only_own_sets(client, user, other_user):
    """Глобальное упражнение видно всем, а подходы в панели — только свои."""
    exercise = ExerciseFactory(name="Жим лёжа", owner=None)
    mine = WorkoutFactory(user=user)
    StrengthSetFactory(workout=mine, exercise=exercise, set_number=1, weight_kg=80, reps=8)
    alien = WorkoutFactory(user=other_user)
    StrengthSetFactory(workout=alien, exercise=exercise, set_number=1, weight_kg=200, reps=1)

    client.force_login(user)
    content = panel(client, exercise).content.decode()

    assert "80 кг" in content
    assert "200" not in content


def test_panel_hides_back_arrow(client, user):
    """В панели кнопка «назад» бессмысленна — список рядом."""
    exercise = ExerciseFactory(name="Жим лёжа", owner=None)

    client.force_login(user)
    in_panel = panel(client, exercise).content.decode()
    as_page = client.get(reverse("exercise_detail", args=[exercise.pk])).content.decode()

    assert "app-screen-head" not in in_panel
    assert "app-screen-head" in as_page


def test_catalog_renders_empty_panel(client, user):
    """Сервер ничего не предвыбирает: он не знает ширину экрана."""
    ExerciseFactory(name="Жим лёжа", owner=None)

    client.force_login(user)
    content = client.get(reverse("exercise_list")).content.decode()

    assert 'id="exercise-panel"' in content
    assert "Выберите упражнение в списке" in content
    assert 'id="exercise-chart"' not in content


def test_catalog_search_form_targets_catalog_url(client, user):
    """Без action форма ушла бы на адрес выбранного упражнения после pushState."""
    ExerciseFactory(name="Жим лёжа", owner=None)

    client.force_login(user)
    content = client.get(reverse("exercise_list")).content.decode()

    assert f'action="{reverse("exercise_list")}"' in content


def test_exercise_detail_highlights_catalog_in_nav(client, user):
    exercise = ExerciseFactory(name="Жим лёжа", owner=None)

    client.force_login(user)
    response = client.get(reverse("exercise_detail", args=[exercise.pk]))

    assert response.context["nav_active"] == "exercises"
