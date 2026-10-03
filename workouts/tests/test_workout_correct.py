"""Правка записанной силовой: подходы, упражнения, заметки после тренировки.

Экран правки — итог, в котором подходы раскрываются степперами живого режима, а
«+ подход» и «+ Упражнение» — те же эндпоинты. Новые подходы записанной
тренировки сразу выполненные и без метки времени. Выполненные подходы идущей
тренировки остаются неприкосновенными — это стерегут отдельные тесты.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from workouts import stats
from workouts.models import Sport, StrengthSet
from workouts.tests.factories import (
    CardioPartFactory,
    ExerciseFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}


@pytest.fixture
def finished(user):
    workout = WorkoutFactory(user=user)
    bench = ExerciseFactory(name="Жим лёжа")
    for number in (1, 2):
        StrengthSetFactory(workout=workout, exercise=bench, set_number=number, weight_kg=80, reps=8)
    return workout


def correct_url(workout, **params):
    url = reverse("workout_correct", args=[workout.pk])
    return url + (f"?set={params['set']}" if "set" in params else "")


# ---------- Экран ----------


def test_correct_screen_requires_login(client, finished):
    response = client.get(correct_url(finished))

    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_other_users_workout_is_404(client, user, other_user):
    alien = WorkoutFactory(user=other_user)
    StrengthSetFactory(workout=alien, set_number=1)

    client.force_login(user)

    assert client.get(correct_url(alien)).status_code == 404


def test_unfinished_workout_redirects_to_live(client, user):
    live = WorkoutFactory(user=user, duration_min=None)

    client.force_login(user)
    response = client.get(correct_url(live))

    assert response.url == reverse("workout_live", args=[live.pk])


def test_workout_without_sets_redirects_to_cardio_form(client, user):
    cardio = CardioPartFactory(workout__user=user).workout

    client.force_login(user)
    response = client.get(correct_url(cardio))

    assert response.url == reverse("workout_edit", args=[cardio.pk])


def test_summary_links_to_correct_screen(client, user, finished):
    client.force_login(user)

    content = client.get(reverse("workout_summary", args=[finished.pk])).content.decode()

    assert f'href="{correct_url(finished)}"' in content
    assert "Изменить тренировку" in content


def test_correct_screen_lists_sets_as_buttons(client, user, finished):
    client.force_login(user)

    content = client.get(correct_url(finished)).content.decode()

    for row in finished.sets.all():
        assert f'hx-get="{correct_url(finished, set=row.pk)}"' in content
    # Ни одного раскрытого подхода, пока на него не нажали.
    assert "set_adjust" not in content and "/adjust/" not in content


def test_tap_on_set_opens_its_steppers(client, user, finished):
    row = finished.sets.order_by("set_number").first()

    client.force_login(user)
    content = client.get(correct_url(finished, set=row.pk), headers=HX).content.decode()

    assert reverse("set_adjust", args=[row.pk]) in content
    assert reverse("set_value", args=[row.pk]) in content
    assert reverse("set_delete", args=[row.pk]) in content
    assert "Подход выполнен" not in content
    assert "<!doctype" not in content.lower()  # регион, а не страница


@pytest.mark.parametrize("count", [1, 6])
def test_correct_screen_queries_do_not_scale(client, user, count, django_assert_max_num_queries):
    workout = WorkoutFactory(user=user)
    for index in range(count):
        exercise = ExerciseFactory(name=f"Упражнение {index}")
        for number in (1, 2, 3):
            StrengthSetFactory(workout=workout, exercise=exercise, set_number=number)

    client.force_login(user)
    with django_assert_max_num_queries(8):
        client.get(correct_url(workout))


# ---------- Правка подходов ----------


def test_adjusting_set_changes_tonnage(client, user, finished):
    row = finished.sets.order_by("set_number").first()

    client.force_login(user)
    client.post(reverse("set_value", args=[row.pk]), {"field": "weight_kg", "value": "100"})

    tonnage = stats.latest_workouts(user)[0]["metric"]
    assert tonnage == "1440 кг"  # 100×8 + 80×8


def test_added_set_is_done_without_timestamp_and_opened(client, user, finished):
    """Новый подход записанной тренировки — сразу выполненный: плановых в ней
    не бывает. Метки времени нет — когда его сделали, неизвестно."""
    bench = finished.sets.first().exercise

    client.force_login(user)
    response = client.post(reverse("live_set_add", args=[finished.pk]), {"exercise": bench.pk})

    added = finished.sets.order_by("-set_number").first()
    assert (added.set_number, added.done, added.done_at) == (3, True, None)
    assert (added.weight_kg, added.reps) == (80, 8)  # повторяет предыдущий
    # Ответ — регион экрана правки, новый подход в нём раскрыт.
    assert reverse("set_adjust", args=[added.pk]) in response.content.decode()


def test_added_exercise_is_done_and_goes_last(client, user, finished):
    squat = ExerciseFactory(name="Присед")

    client.force_login(user)
    response = client.post(reverse("live_exercises", args=[finished.pk]), {"exercise": squat.pk})

    new_sets = list(finished.sets.filter(exercise=squat))
    assert new_sets and all(row.done and row.done_at is None for row in new_sets)
    content = response.content.decode()
    assert 'hx-swap-oob="true"' in content
    assert content.index("Жим лёжа") < content.index("Присед")


def test_deleting_set_of_finished_workout(client, user, finished):
    row = finished.sets.order_by("set_number").last()

    client.force_login(user)
    client.post(reverse("set_delete", args=[row.pk]))

    assert not StrengthSet.objects.filter(pk=row.pk).exists()


def test_last_set_of_finished_workout_is_kept(client, user):
    """Пустая силовая перестала бы быть силовой: убрать всё — «Удалить тренировку»."""
    workout = WorkoutFactory(user=user)
    row = StrengthSetFactory(workout=workout, set_number=1)

    client.force_login(user)
    response = client.post(reverse("set_delete", args=[row.pk]))

    assert StrengthSet.objects.filter(pk=row.pk).exists()
    assert "удалите тренировку" in response.content.decode()


def test_last_set_offers_no_delete_button(client, user):
    workout = WorkoutFactory(user=user)
    row = StrengthSetFactory(workout=workout, set_number=1)

    client.force_login(user)
    content = client.get(correct_url(workout, set=row.pk), headers=HX).content.decode()

    assert reverse("set_delete", args=[row.pk]) not in content


# ---------- Что остаётся закрытым ----------


@pytest.mark.parametrize("url_name", ["set_done", "set_undo"])
def test_done_and_undo_stay_live_only(client, user, finished, url_name):
    """Выполнить и вернуть в работу — действия идущего времени, у записанной их нет."""
    row = finished.sets.first()

    client.force_login(user)

    assert client.post(reverse(url_name, args=[row.pk])).status_code == 404


def test_live_only_actions_reject_finished_workout(client, user, finished):
    bench = finished.sets.first().exercise

    client.force_login(user)

    select = client.post(
        reverse("live_exercise_select", args=[finished.pk]), {"exercise": bench.pk}
    )
    rest = client.post(reverse("live_rest", args=[finished.pk]), {"delta": "15"})
    assert select.status_code == 404
    assert rest.status_code == 404


def test_cardio_workout_without_sets_gets_no_set_editing(client, user):
    cardio = WorkoutFactory(user=user, sport__category=Sport.Category.CARDIO)
    bench = ExerciseFactory(name="Жим лёжа")

    client.force_login(user)
    response = client.post(reverse("live_exercises", args=[cardio.pk]), {"exercise": bench.pk})

    assert response.status_code == 404


# ---------- Изоляция ----------


def test_other_users_finished_workout_cannot_be_changed(client, user, other_user):
    alien = WorkoutFactory(user=other_user)
    bench = ExerciseFactory(name="Жим лёжа")
    row = StrengthSetFactory(workout=alien, exercise=bench, set_number=1, weight_kg=80)

    client.force_login(user)
    responses = [
        client.post(reverse("set_value", args=[row.pk]), {"field": "weight_kg", "value": "1"}),
        client.post(reverse("set_adjust", args=[row.pk]), {"field": "weight_kg", "dir": "up"}),
        client.post(reverse("set_delete", args=[row.pk])),
        client.post(reverse("live_set_add", args=[alien.pk]), {"exercise": bench.pk}),
        client.post(reverse("live_exercises", args=[alien.pk]), {"exercise": bench.pk}),
        client.post(reverse("live_note", args=[alien.pk]), {"exercise": bench.pk, "text": "чужое"}),
    ]

    assert [response.status_code for response in responses] == [404] * 6
    row.refresh_from_db()
    assert row.weight_kg == Decimal("80")
    assert alien.sets.count() == 1
