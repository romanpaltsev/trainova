"""Заметка к упражнению внутри тренировки: «болело плечо», «узкий хват».

Заметка привязана к паре (тренировка, упражнение), пишется в живом режиме и в
черновике, а в записанной тренировке только читается. «Заметки нет» — это
отсутствие строки, а не пустой текст.
"""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from workouts import stats
from workouts.models import NOTE_MAX_LENGTH, ExerciseNote, Workout
from workouts.tests.factories import (
    ExerciseFactory,
    ExerciseNoteFactory,
    SportFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def active(user, **kwargs):
    return WorkoutFactory(user=user, duration_min=None, **kwargs)


def days_ago(days):
    return timezone.now() - timedelta(days=days)


def note_url(workout):
    return reverse("live_note", args=[workout.pk])


def save_note(client, workout, exercise, text):
    return client.post(note_url(workout), {"exercise": exercise.pk, "text": text})


def open_note(client, workout, exercise):
    return client.get(note_url(workout), {"exercise": exercise.pk})


# ---------- Изоляция: сначала самое опасное ----------


def test_exercise_page_hides_other_users_notes(client, user, other_user):
    """Страница глобального упражнения открыта всем — заметка чужой тренировки
    туда попасть не должна: это самый личный текст в приложении."""
    bench = ExerciseFactory(name="Жим лёжа", owner=None)
    mine = WorkoutFactory(user=user)
    StrengthSetFactory(workout=mine, exercise=bench, set_number=1)
    ExerciseNoteFactory(workout=mine, exercise=bench, text="моя заметка")
    alien = WorkoutFactory(user=other_user)
    StrengthSetFactory(workout=alien, exercise=bench, set_number=1)
    ExerciseNoteFactory(workout=alien, exercise=bench, text="чужая заметка")

    client.force_login(user)
    content = client.get(reverse("exercise_detail", args=[bench.pk])).content.decode()

    assert "моя заметка" in content
    assert "чужая заметка" not in content
    assert [group["note"] for group in stats.exercise_progress(user, bench)] == ["моя заметка"]


@pytest.mark.parametrize("method", ["get", "post"])
def test_note_of_foreign_workout_is_404(client, user, other_user, method):
    alien = active(other_user)
    exercise = ExerciseFactory()
    StrengthSetFactory(workout=alien, exercise=exercise, set_number=1, done=False)

    client.force_login(user)
    payload = {"exercise": exercise.pk, "text": "подмена"}
    request = getattr(client, method)
    response = request(note_url(alien), payload)

    assert response.status_code == 404
    assert ExerciseNote.objects.exists() is False


def test_exercise_outside_this_workout_is_404(client, user):
    workout = active(user)
    StrengthSetFactory(workout=workout, set_number=1, done=False)
    stranger = ExerciseFactory(name="Не из этой тренировки")

    client.force_login(user)
    response = save_note(client, workout, stranger, "мимо")

    assert response.status_code == 404
    assert ExerciseNote.objects.exists() is False


def test_finished_workout_note_is_read_only(client, user):
    finished = WorkoutFactory(user=user)
    exercise = ExerciseFactory(name="Жим лёжа")
    StrengthSetFactory(workout=finished, exercise=exercise, set_number=1)
    ExerciseNoteFactory(workout=finished, exercise=exercise, text="болело плечо")

    client.force_login(user)
    summary = client.get(reverse("workout_summary", args=[finished.pk])).content.decode()

    # Читается в итоге, но правки после записи нет.
    assert "болело плечо" in summary
    assert open_note(client, finished, exercise).status_code == 404
    assert save_note(client, finished, exercise, "правка").status_code == 404
    assert ExerciseNote.objects.get().text == "болело плечо"


# ---------- Запись ----------


def test_note_is_saved_and_shown_on_live_screen(client, user):
    workout = active(user)
    exercise = ExerciseFactory(name="Жим лёжа")
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=False)

    client.force_login(user)
    response = save_note(client, workout, exercise, "  узкий хват  ")

    assert response.status_code == 200
    assert ExerciseNote.objects.get().text == "узкий хват"
    content = client.get(reverse("workout_live", args=[workout.pk])).content.decode()
    assert "узкий хват" in content


def test_modal_shows_current_text(client, user):
    workout = active(user)
    exercise = ExerciseFactory()
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=False)
    ExerciseNoteFactory(workout=workout, exercise=exercise, text="болело плечо")

    client.force_login(user)
    content = open_note(client, workout, exercise).content.decode()

    assert "болело плечо" in content
    assert 'name="text"' in content


def test_second_save_updates_the_same_row(client, user):
    workout = active(user)
    exercise = ExerciseFactory()
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=False)

    client.force_login(user)
    save_note(client, workout, exercise, "первая")
    save_note(client, workout, exercise, "вторая")

    assert ExerciseNote.objects.count() == 1
    assert ExerciseNote.objects.get().text == "вторая"


@pytest.mark.parametrize("text", ["", "   ", "\n"], ids=["пусто", "пробелы", "перевод-строки"])
def test_blank_text_removes_the_note(client, user, text):
    """«Заметки нет» — это отсутствие строки: пустого текста в базе не бывает."""
    workout = active(user)
    exercise = ExerciseFactory()
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=False)
    ExerciseNoteFactory(workout=workout, exercise=exercise, text="было")

    client.force_login(user)
    save_note(client, workout, exercise, text)

    assert ExerciseNote.objects.exists() is False
    content = client.get(reverse("workout_live", args=[workout.pk])).content.decode()
    assert "Заметка" in content  # кнопка вернулась в пустое состояние


def test_too_long_note_shows_error_and_is_not_saved(client, user):
    workout = active(user)
    exercise = ExerciseFactory()
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=False)

    client.force_login(user)
    content = save_note(client, workout, exercise, "я" * (NOTE_MAX_LENGTH + 1)).content.decode()

    assert "Заметка слишком длинная." in content
    assert ExerciseNote.objects.exists() is False


def test_note_is_editable_in_a_draft(client, user):
    """Черновик — тот же экран: заметку можно написать заранее, готовя тренировку."""
    draft = WorkoutFactory(user=user, started_at=None, duration_min=None)
    exercise = ExerciseFactory()
    StrengthSetFactory(workout=draft, exercise=exercise, set_number=1, done=False)

    client.force_login(user)
    save_note(client, draft, exercise, "попробовать узкий хват")

    assert ExerciseNote.objects.get().text == "попробовать узкий хват"


def test_saving_note_does_not_restart_the_rest_timer(client, user):
    """Ответ обновляет только регион упражнений: отдых посреди тренировки
    перезапускаться не должен."""
    workout = active(user)
    exercise = ExerciseFactory()
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=False)

    client.force_login(user)
    content = save_note(client, workout, exercise, "заметка").content.decode()

    assert 'hx-swap-oob="true"' in content
    assert "data-autostart" not in content
    assert 'id="rest-card"' not in content


# ---------- Заметка видна во всех состояниях упражнения ----------


def test_note_stays_visible_when_exercise_is_done(client, user):
    """Отметил последний подход — упражнение уехало в «Выполнено», но заметка
    должна остаться на экране и остаться правимой."""
    workout = active(user)
    exercise = ExerciseFactory(name="Жим лёжа")
    row = StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=False)
    ExerciseNoteFactory(workout=workout, exercise=exercise, text="узкий хват")

    client.force_login(user)
    client.post(reverse("set_done", args=[row.pk]))
    content = client.get(reverse("workout_live", args=[workout.pk])).content.decode()

    assert "Выполнено" in content
    assert "узкий хват" in content
    assert save_note(client, workout, exercise, "правка").status_code == 200


def test_note_of_queued_exercise_is_shown(client, user):
    workout = active(user)
    current = ExerciseFactory(name="Жим лёжа")
    queued = ExerciseFactory(name="Присед со штангой")
    StrengthSetFactory(workout=workout, exercise=current, set_number=1, done=False)
    StrengthSetFactory(workout=workout, exercise=queued, set_number=1, done=False)
    ExerciseNoteFactory(workout=workout, exercise=queued, text="ноги после болезни")

    client.force_login(user)
    content = client.get(reverse("workout_live", args=[workout.pk])).content.decode()

    assert "Дальше" in content
    assert "ноги после болезни" in content


# ---------- Прошлая заметка ----------


def test_previous_note_comes_from_the_workout_that_prefilled_sets(client, user):
    bench = ExerciseFactory(name="Жим лёжа")
    past = WorkoutFactory(user=user)
    StrengthSetFactory(workout=past, exercise=bench, set_number=1, weight_kg=70, reps=10)
    ExerciseNoteFactory(workout=past, exercise=bench, text="болело плечо")
    workout = active(user)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1, done=False)

    client.force_login(user)
    content = client.get(reverse("workout_live", args=[workout.pk])).content.decode()

    assert "прошлый раз: 70×10" in content
    assert "заметка в прошлый раз: болело плечо" in content


def test_previous_note_is_absent_when_the_last_workout_had_none(client, user):
    """Подсказка «прошлый раз» говорит об одной конкретной тренировке — и заметка
    рядом должна быть про неё же, а не про какую-то давнюю."""
    bench = ExerciseFactory(name="Жим лёжа")
    older = WorkoutFactory(user=user, started_at=days_ago(14))
    StrengthSetFactory(workout=older, exercise=bench, set_number=1, weight_kg=60, reps=10)
    ExerciseNoteFactory(workout=older, exercise=bench, text="давняя заметка")
    recent = WorkoutFactory(user=user, started_at=days_ago(7))
    StrengthSetFactory(workout=recent, exercise=bench, set_number=1, weight_kg=70, reps=10)
    workout = active(user)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1, done=False)

    client.force_login(user)
    content = client.get(reverse("workout_live", args=[workout.pk])).content.decode()

    assert "заметка в прошлый раз" not in content
    assert "давняя заметка" not in content


def test_own_note_is_not_echoed_as_previous(client, user):
    bench = ExerciseFactory(name="Жим лёжа")
    workout = active(user)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1, done=False)
    ExerciseNoteFactory(workout=workout, exercise=bench, text="сегодняшняя")

    client.force_login(user)
    content = client.get(reverse("workout_live", args=[workout.pk])).content.decode()

    assert "заметка в прошлый раз" not in content


def test_other_users_note_is_not_a_previous_source(client, user, other_user):
    bench = ExerciseFactory(name="Жим лёжа", owner=None)
    alien = WorkoutFactory(user=other_user)
    StrengthSetFactory(workout=alien, exercise=bench, set_number=1, weight_kg=200, reps=1)
    ExerciseNoteFactory(workout=alien, exercise=bench, text="чужая заметка")
    workout = active(user)
    StrengthSetFactory(workout=workout, exercise=bench, set_number=1, done=False)

    client.force_login(user)
    content = client.get(reverse("workout_live", args=[workout.pk])).content.decode()

    assert "чужая заметка" not in content


# ---------- Жизненный цикл ----------


def test_note_dies_with_the_last_set_of_the_exercise(client, user):
    """Упражнения в тренировке больше нет — значит, и заметке там не место:
    иначе она всплыла бы при повторном добавлении."""
    workout = active(user)
    exercise = ExerciseFactory()
    row = StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=False)
    ExerciseNoteFactory(workout=workout, exercise=exercise, text="исчезнет")

    client.force_login(user)
    client.post(reverse("set_delete", args=[row.pk]))

    assert ExerciseNote.objects.exists() is False


def test_note_survives_while_any_set_remains(client, user):
    workout = active(user)
    exercise = ExerciseFactory()
    first = StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=False)
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=2, done=False)
    ExerciseNoteFactory(workout=workout, exercise=exercise, text="останется")

    client.force_login(user)
    client.post(reverse("set_delete", args=[first.pk]))

    assert ExerciseNote.objects.get().text == "останется"


def test_note_of_planned_only_exercise_is_dropped_on_finish(client, user):
    """Невыполненные подходы при завершении удаляются — и заметка вместе с ними."""
    workout = active(user)
    done_exercise = ExerciseFactory(name="Жим лёжа")
    skipped = ExerciseFactory(name="Присед со штангой")
    StrengthSetFactory(workout=workout, exercise=done_exercise, set_number=1, done=True)
    StrengthSetFactory(workout=workout, exercise=skipped, set_number=1, done=False)
    ExerciseNoteFactory(workout=workout, exercise=done_exercise, text="сделал")
    ExerciseNoteFactory(workout=workout, exercise=skipped, text="не дошёл")

    client.force_login(user)
    client.post(reverse("workout_finish", args=[workout.pk]))

    assert [note.text for note in ExerciseNote.objects.all()] == ["сделал"]


def test_workout_delete_removes_its_notes(user):
    workout = active(user)
    exercise = ExerciseFactory()
    StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, done=False)
    ExerciseNoteFactory(workout=workout, exercise=exercise, text="уйдёт каскадом")

    workout.delete()

    assert ExerciseNote.objects.exists() is False


def test_personal_exercise_with_orphan_note_can_still_be_deleted(client, user):
    """CASCADE, а не PROTECT: осиротевшая заметка не должна делать упражнение
    неудаляемым — убрать её из интерфейса всё равно было бы нечем."""
    workout = active(user)
    exercise = ExerciseFactory(owner=user, name="Моё упражнение")
    ExerciseNoteFactory(workout=workout, exercise=exercise, text="осиротела")

    client.force_login(user)
    response = client.post(reverse("exercise_delete", args=[exercise.pk]))

    assert response.status_code == 302
    assert ExerciseNote.objects.exists() is False


def test_repeat_does_not_copy_notes(client, user):
    """Заметка про тот раз, а не про упражнение вообще: в новой тренировке она
    покажется подсказкой «в прошлый раз», но не скопируется."""
    source = WorkoutFactory(user=user, sport=SportFactory(name="Силовая"))
    exercise = ExerciseFactory(name="Жим лёжа")
    StrengthSetFactory(workout=source, exercise=exercise, set_number=1)
    ExerciseNoteFactory(workout=source, exercise=exercise, text="болело плечо")

    client.force_login(user)
    client.post(reverse("workout_repeat", args=[source.pk]))

    repeated = Workout.objects.filter(user=user).live().get()
    assert repeated.exercise_notes.exists() is False
    assert ExerciseNote.objects.count() == 1


def test_one_note_per_workout_and_exercise(user):
    workout = active(user)
    exercise = ExerciseFactory()
    ExerciseNoteFactory(workout=workout, exercise=exercise, text="первая")

    with pytest.raises(IntegrityError), transaction.atomic():
        ExerciseNoteFactory(workout=workout, exercise=exercise, text="вторая")
