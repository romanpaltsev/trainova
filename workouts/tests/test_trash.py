"""Корзина «Недавно удалённые»: снимок при удалении, восстановление, чистка.

Удаление тренировки по-прежнему удаляет её строки, но снимок 30 дней лежит в
DeletedWorkout. Восстановление — настоящая отмена: те же pk, те же значения,
пропавшие справочники находятся по имени.
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from workouts import trash
from workouts.models import (
    CardioPart,
    DeletedWorkout,
    Exercise,
    ExerciseNote,
    Location,
    Sport,
    StrengthSet,
    Workout,
)
from workouts.tests.budgets import SIDEBAR_QUERIES
from workouts.tests.factories import (
    CardioPartFactory,
    ExerciseFactory,
    ExerciseNoteFactory,
    LocationFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def delete(client, workout):
    return client.post(reverse("workout_delete", args=[workout.pk]))


def restore(client, entry):
    return client.post(reverse("workout_restore", args=[entry.pk]))


def rows(workout_pk):
    """Всё, что составляет тренировку, — для сравнения «до» и «после»."""
    return {
        "workout": list(
            Workout.objects.filter(pk=workout_pk).values_list(
                "pk", "sport_id", "location_id", "started_at", "duration_min", "note", "planned_for"
            )
        ),
        "sets": list(
            StrengthSet.objects.filter(workout_id=workout_pk)
            .order_by("pk")
            .values_list(
                "pk",
                "exercise_id",
                "set_number",
                "weight_kg",
                "reps",
                "done",
                "done_at",
                "measurement",
            )
        ),
        "parts": list(
            CardioPart.objects.filter(workout_id=workout_pk)
            .order_by("pk")
            .values_list("pk", "sport_id", "distance_km", "duration_min")
        ),
        "notes": list(
            ExerciseNote.objects.filter(workout_id=workout_pk)
            .order_by("pk")
            .values_list("exercise_id", "text")
        ),
    }


@pytest.fixture
def finished(user):
    """Смешанная записанная: подходы с точными метками, кардио-часть, заметки, место."""
    place = LocationFactory(owner=user, name="СпортЛайф")
    workout = WorkoutFactory(user=user, location=place, note="тяжело")
    bench = ExerciseFactory(name="Жим лёжа")
    done_at = timezone.now().replace(microsecond=123456)
    for number in (1, 2):
        StrengthSetFactory(
            workout=workout,
            exercise=bench,
            set_number=number,
            weight_kg=Decimal("82.5"),
            done_at=done_at + timedelta(minutes=number),
        )
    ExerciseNoteFactory(workout=workout, exercise=bench, text="узкий хват")
    CardioPartFactory(
        workout=workout, sport=SportFactory(category=Sport.Category.CARDIO), distance_km=3
    )
    return workout


# ---------- Удаление ----------


def test_delete_moves_workout_to_trash_and_offers_restore(client, user, finished):
    pk = finished.pk
    client.force_login(user)

    response = delete(client, finished)

    assert response.url == reverse("workout_history")
    assert rows(pk) == {"workout": [], "sets": [], "parts": [], "notes": []}
    entry = DeletedWorkout.objects.get(user=user)
    assert entry.title and entry.color_key
    page = client.get(response.url).content.decode()
    assert "Тренировка удалена." in page
    assert "app-alert app-alert-success has-action" in page
    assert reverse("workout_restore", args=[entry.pk]) in page


def test_draft_goes_to_trash_too(client, user):
    draft = WorkoutFactory(user=user, started_at=None, duration_min=None)
    StrengthSetFactory(workout=draft, set_number=1, done=False)
    client.force_login(user)

    response = delete(client, draft)

    assert response.url == reverse("dashboard")
    entry = DeletedWorkout.objects.get(user=user)
    assert entry.subtitle.startswith("Черновик")


def test_confirm_page_promises_thirty_days(client, user, finished):
    client.force_login(user)

    content = client.get(reverse("workout_delete", args=[finished.pk])).content.decode()

    assert "30 дней её можно будет вернуть" in content
    assert "Действие нельзя отменить" not in content


# ---------- Восстановление ----------


def test_restore_brings_back_the_same_rows(client, user, finished):
    before = rows(finished.pk)
    client.force_login(user)
    delete(client, finished)
    entry = DeletedWorkout.objects.get(user=user)

    response = restore(client, entry)

    assert response.url == reverse("workout_summary", args=[finished.pk])
    assert rows(finished.pk) == before
    assert not DeletedWorkout.objects.exists()
    page = client.get(response.url, follow=True).content.decode()
    assert "Тренировка восстановлена." in page


@pytest.mark.parametrize(
    ("kind", "url_name"),
    [
        ("strength-draft", "workout_live"),
        ("cardio-draft", "workout_edit"),
        ("live", "workout_live"),
    ],
)
def test_restore_leads_to_the_home_of_the_workout(client, user, kind, url_name):
    if kind == "cardio-draft":
        workout = WorkoutFactory(
            user=user, started_at=None, duration_min=None, sport__category=Sport.Category.CARDIO
        )
    else:
        started_at = None if kind == "strength-draft" else timezone.now()
        workout = WorkoutFactory(user=user, started_at=started_at, duration_min=None)
        StrengthSetFactory(workout=workout, set_number=1, done=False)
    client.force_login(user)
    delete(client, workout)

    response = restore(client, DeletedWorkout.objects.get(user=user))

    assert response.url == reverse(url_name, args=[workout.pk])


def test_draft_keeps_its_planned_day(client, user):
    day = timezone.localdate() + timedelta(days=2)
    draft = WorkoutFactory(user=user, started_at=None, duration_min=None, planned_for=day)
    StrengthSetFactory(workout=draft, set_number=1, done=False)
    client.force_login(user)
    delete(client, draft)

    restore(client, DeletedWorkout.objects.get(user=user))

    assert Workout.objects.get(pk=draft.pk).planned_for == day


def test_live_workout_waits_until_the_current_one_is_finished(client, user):
    first = WorkoutFactory(user=user, duration_min=None)
    StrengthSetFactory(workout=first, set_number=1, done=False)
    client.force_login(user)
    delete(client, first)
    entry = DeletedWorkout.objects.get(user=user)
    second = WorkoutFactory(user=user, duration_min=None)

    refused = restore(client, entry)

    assert refused.url == reverse("workout_trash")
    assert DeletedWorkout.objects.filter(pk=entry.pk).exists()
    page = client.get(refused.url).content.decode()
    assert "Сначала завершите текущую тренировку." in page

    Workout.objects.filter(pk=second.pk).update(duration_min=30)
    assert restore(client, entry).url == reverse("workout_live", args=[first.pk])


def test_deleted_own_catalog_items_are_found_again_by_name(client, user):
    """Своё упражнение, вид спорта и место удалили, пока тренировка лежала в
    корзине, — их больше ничего не держит. Восстановление заводит их заново по
    имени с прежними атрибутами: тот же контракт, что у импорта Excel."""
    sport = SportFactory(owner=user, name="Кроссфит")
    place = LocationFactory(owner=user, name="Гараж")
    own = ExerciseFactory(
        owner=user,
        name="Мой жим",
        measurement=Exercise.Measurement.REPS,
        muscle_group="Плечи",
        equipment="Гантели",
    )
    workout = WorkoutFactory(user=user, sport=sport, location=place)
    StrengthSetFactory(
        workout=workout, exercise=own, set_number=1, measurement="reps", weight_kg=0, reps=12
    )
    client.force_login(user)
    delete(client, workout)
    Exercise.objects.filter(pk=own.pk).delete()
    Sport.objects.filter(pk=sport.pk).delete()
    Location.objects.filter(pk=place.pk).delete()

    restore(client, DeletedWorkout.objects.get(user=user))

    workout = Workout.objects.get(pk=workout.pk)
    exercise = workout.sets.get().exercise
    assert (exercise.owner, exercise.name, exercise.measurement) == (user, "Мой жим", "reps")
    assert (exercise.muscle_group, exercise.equipment) == ("Плечи", "Гантели")
    assert (workout.sport.name, workout.sport.owner) == ("Кроссфит", user)
    assert workout.location.name == "Гараж"


def test_renamed_exercise_is_kept_by_id(client, user):
    own = ExerciseFactory(owner=user, name="Тяга")
    workout = WorkoutFactory(user=user)
    StrengthSetFactory(workout=workout, exercise=own, set_number=1)
    client.force_login(user)
    delete(client, workout)
    Exercise.objects.filter(pk=own.pk).update(name="Тяга сидя")
    total = Exercise.objects.count()

    restore(client, DeletedWorkout.objects.get(user=user))

    assert Workout.objects.get(pk=workout.pk).sets.get().exercise_id == own.pk
    assert Exercise.objects.count() == total


def test_two_exercises_merging_into_one_are_renumbered(client, user):
    """Своё «Жим» удалили, а общее с тем же именем есть: оба сходятся в общее,
    и номера подходов перенумеровываются, а заметка остаётся первая."""
    common = ExerciseFactory(name="Жим")
    own = ExerciseFactory(owner=user, name="Жим")
    workout = WorkoutFactory(user=user)
    for exercise in (common, own):
        for number in (1, 2):
            StrengthSetFactory(workout=workout, exercise=exercise, set_number=number)
        ExerciseNoteFactory(workout=workout, exercise=exercise, text=f"заметка {exercise.pk}")
    client.force_login(user)
    delete(client, workout)
    Exercise.objects.filter(pk=own.pk).delete()

    restore(client, DeletedWorkout.objects.get(user=user))

    restored = Workout.objects.get(pk=workout.pk)
    assert sorted(restored.sets.values_list("set_number", flat=True)) == [1, 2, 3, 4]
    assert set(restored.sets.values_list("exercise_id", flat=True)) == {common.pk}
    assert list(restored.exercise_notes.values_list("text", flat=True)) == [f"заметка {common.pk}"]


def test_snapshot_survives_unknown_and_missing_fields(user, finished):
    """Снимок пережил миграцию: лишнее поле игнорируется, пропавшее берёт умолчание."""
    entry = trash.move_to_trash(finished, title="Грудь", subtitle="")
    entry.refresh_from_db()
    fields = entry.payload["objects"][0]["fields"]
    fields["legacy_field"] = 1
    del fields["note"]
    entry.save()

    workout = trash.restore(entry.pk, user)

    assert Workout.objects.get(pk=workout.pk).note == ""


def test_second_restore_is_404_and_creates_nothing(client, user, finished):
    client.force_login(user)
    delete(client, finished)
    entry = DeletedWorkout.objects.get(user=user)
    restore(client, entry)

    assert restore(client, entry).status_code == 404
    assert Workout.objects.filter(user=user).count() == 1


def test_restore_is_post_only(client, user, finished):
    client.force_login(user)
    delete(client, finished)

    response = client.get(
        reverse("workout_restore", args=[DeletedWorkout.objects.get(user=user).pk])
    )

    assert response.status_code == 405


# ---------- Срок и чистка ----------


def test_expired_entry_is_hidden_refused_and_purged(client, user, finished):
    client.force_login(user)
    delete(client, finished)
    entry = DeletedWorkout.objects.get(user=user)
    DeletedWorkout.objects.filter(pk=entry.pk).update(
        deleted_at=timezone.now() - timedelta(days=31)
    )

    assert entry.title not in client.get(reverse("workout_trash")).content.decode()
    refused = restore(client, entry)
    assert refused.url == reverse("workout_trash")
    assert not Workout.objects.filter(pk=finished.pk).exists()

    call_command("purge_deleted", "--dry-run")
    assert DeletedWorkout.objects.filter(pk=entry.pk).exists()
    call_command("purge_deleted")
    assert not DeletedWorkout.objects.filter(pk=entry.pk).exists()


def test_purge_keeps_fresh_entries(user, finished):
    trash.move_to_trash(finished, title="Грудь", subtitle="")

    call_command("purge_deleted")

    assert DeletedWorkout.objects.count() == 1


def test_backup_script_purges_after_verified_dump():
    """Чистка — только после удачного дампа: удалённое насовсем остаётся в бэкапе."""
    script = (Path(settings.BASE_DIR) / "scripts" / "backup.sh").read_text()

    assert "manage.py purge_deleted" in script
    assert script.index("pg_restore --list") < script.index("manage.py purge_deleted")


# ---------- Где видно ----------


def test_trash_page_lists_entries_with_days_left(client, user, finished):
    client.force_login(user)
    delete(client, finished)
    entry = DeletedWorkout.objects.get(user=user)

    content = client.get(reverse("workout_trash")).content.decode()

    assert entry.title in content
    assert "удалено сегодня" in content
    assert "ещё 30 дней" in content
    assert reverse("workout_restore", args=[entry.pk]) in content


def test_empty_trash_page(client, user):
    client.force_login(user)

    assert "Здесь пусто" in client.get(reverse("workout_trash")).content.decode()


def test_profile_counts_fresh_own_entries(client, user, other_user):
    for _ in range(2):
        trash.move_to_trash(WorkoutFactory(user=user), title="Грудь", subtitle="")
    stale = trash.move_to_trash(WorkoutFactory(user=user), title="Грудь", subtitle="")
    DeletedWorkout.objects.filter(pk=stale.pk).update(
        deleted_at=timezone.now() - timedelta(days=40)
    )
    trash.move_to_trash(WorkoutFactory(user=other_user), title="Чужое", subtitle="")
    client.force_login(user)

    response = client.get(reverse("profile"))

    assert response.context["trash_count"] == 2
    assert reverse("workout_trash") in response.content.decode()


def test_history_links_to_trash(client, user):
    client.force_login(user)

    assert reverse("workout_trash") in client.get(reverse("workout_history")).content.decode()


# ---------- Изоляция и бюджет ----------


def test_other_users_trash_is_invisible_and_not_restorable(client, user, other_user):
    alien = WorkoutFactory(user=other_user)
    StrengthSetFactory(workout=alien, set_number=1)
    entry = trash.move_to_trash(alien, title="Чужая тренировка", subtitle="")
    client.force_login(user)

    assert "Чужая тренировка" not in client.get(reverse("workout_trash")).content.decode()
    assert restore(client, entry).status_code == 404
    assert not Workout.objects.filter(pk=alien.pk).exists()
    assert DeletedWorkout.objects.filter(pk=entry.pk).exists()


def test_other_users_workout_cannot_be_trashed(client, user, other_user):
    alien = WorkoutFactory(user=other_user)
    client.force_login(user)

    assert delete(client, alien).status_code == 404
    assert Workout.objects.filter(pk=alien.pk).exists()
    assert not DeletedWorkout.objects.exists()


@pytest.mark.parametrize("count", [1, 5])
def test_trash_page_queries_do_not_scale(client, user, count, django_assert_max_num_queries):
    for _ in range(count):
        trash.move_to_trash(WorkoutFactory(user=user), title="Грудь", subtitle="")
    client.force_login(user)

    # Транзакция запроса (SAVEPOINT и RELEASE), сессия, пользователь и сама корзина.
    with django_assert_max_num_queries(5 + SIDEBAR_QUERIES):
        client.get(reverse("workout_trash"))


def test_restore_keeps_old_entries_of_other_days(client, user):
    """Корзина — не одна последняя тренировка: восстановление одной не трогает другие."""
    first, second = (
        WorkoutFactory(user=user),
        WorkoutFactory(user=user, started_at=None, duration_min=None),
    )
    client.force_login(user)
    delete(client, first)
    delete(client, second)
    entries = list(DeletedWorkout.objects.filter(user=user))

    restore(client, entries[0])

    assert DeletedWorkout.objects.filter(user=user).count() == 1
