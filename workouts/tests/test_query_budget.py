"""Границы числа запросов: число не должно расти вместе с данными."""

import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from workouts import exercise_excel
from workouts.models import Exercise, Sport
from workouts.tests.factories import (
    CardioPartFactory,
    ExerciseFactory,
    ExerciseNoteFactory,
    ExerciseSettingsFactory,
    LocationFactory,
    StrengthSetFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db


def fill_history(user, weeks=6):
    """Немного истории: силовые с подходами и кардио — как у живого пользователя."""
    bench = ExerciseFactory(name="Жим лёжа")
    squat = ExerciseFactory(name="Присед со штангой")
    for week in range(weeks):
        started = timezone.now() - timedelta(weeks=week, days=1)
        workout = WorkoutFactory(user=user, started_at=started)
        for number, exercise in enumerate((bench, squat), start=1):
            StrengthSetFactory(
                workout=workout, exercise=exercise, set_number=number, weight_kg=70 + week, reps=8
            )
        CardioPartFactory(
            workout__user=user, workout__started_at=started - timedelta(days=2), distance_km=10
        )
    return bench


def fill_drafts(user, count=1):
    """Подготовленные тренировки для блока «Подготовлено».

    Кардио берём и с целью, и без неё: ярлык плана читает обратную OneToOne, и
    без select_related каждая такая строка спрашивала бы её отдельным запросом.
    """
    bench = ExerciseFactory(name="Жим стоя")
    bike = Sport.objects.get_or_create(
        name="Велосипед", owner=None, category=Sport.Category.CARDIO
    )[0]
    for index in range(count):
        strength = WorkoutFactory(user=user, started_at=None, duration_min=None)
        StrengthSetFactory(workout=strength, exercise=bench, set_number=1, done=False)
        CardioPartFactory(
            workout__user=user,
            workout__sport=bike,
            workout__started_at=None,
            workout__duration_min=None,
            distance_km=10 + index,
        )
        WorkoutFactory(user=user, sport=bike, started_at=None, duration_min=None)


@pytest.mark.parametrize("drafts", [1, 4], ids=["one-draft", "four-drafts"])
def test_dashboard_query_budget(client, user, django_assert_max_num_queries, drafts):
    """Дашборд собирает подготовленное, сводку, график, рекорды и последние.

    Два запроса — подписи по группам мышц: по одному агрегату на блок
    «Подготовлено» и на «Последние тренировки», а не по строке. Ещё четыре —
    кардио-части: prefetch на те же два блока и по запросу на окна сводки и на
    график, где части забираются отдельно от тренировок (джойн размножил бы
    строки и задвоил минуты). Число черновиков параметризовано: именно оно
    поймало бы N+1 по цели кардио-плана.
    """
    fill_history(user)
    fill_drafts(user, drafts)

    client.force_login(user)
    with django_assert_max_num_queries(22):
        client.get(reverse("dashboard"))


def test_dashboard_queries_do_not_scale_with_history(client, user, django_assert_max_num_queries):
    fill_history(user, weeks=12)
    fill_drafts(user, 4)

    client.force_login(user)
    with django_assert_max_num_queries(22):
        client.get(reverse("dashboard"))


def test_exercise_page_query_budget(client, user, django_assert_max_num_queries):
    bench = fill_history(user)

    client.force_login(user)
    # Седьмой запрос — заметки упражнения (один на всю историю), восьмой — список
    # групп мышц для чипов, девятый — позиции упражнения в тренировках («каким
    # по счёту делал»). Ни один не зависит от объёма истории.
    with django_assert_max_num_queries(9):
        client.get(reverse("exercise_detail", args=[bench.pk]))


def test_exercise_page_queries_do_not_scale_with_history(
    client, user, django_assert_max_num_queries
):
    """Позиции считаются одним агрегатом на всю историю, а не по тренировке."""
    bench = fill_history(user, weeks=12)

    client.force_login(user)
    with django_assert_max_num_queries(9):
        client.get(reverse("exercise_detail", args=[bench.pk]))


def test_exercise_panel_query_budget(client, user, django_assert_max_num_queries):
    """Панель мастер-детали — та же вьюха: партиал не должен добавлять запросов."""
    bench = fill_history(user)

    client.force_login(user)
    with django_assert_max_num_queries(9):
        client.get(reverse("exercise_detail", args=[bench.pk]), headers={"HX-Request": "true"})


def test_catalog_query_budget(client, user, django_assert_max_num_queries):
    fill_history(user)

    client.force_login(user)
    with django_assert_max_num_queries(7):
        client.get(reverse("exercise_list"))


def test_catalog_query_budget_with_both_facets(client, user, django_assert_max_num_queries):
    """Обе оси чипов берутся одним запросом парами — иначе здесь был бы восьмой.

    Фильтры активны намеренно: и чипы, и оба фильтра спрашивают один и тот же
    список, и кеш facets() обязан это выдержать.
    """
    bench = fill_history(user)
    Exercise.objects.filter(pk=bench.pk).update(equipment="Штанга")

    client.force_login(user)
    with django_assert_max_num_queries(7):
        client.get(reverse("exercise_list"), {"equipment": "Штанга", "group": bench.muscle_group})


@pytest.mark.parametrize("queued", [1, 6], ids=["one-exercise", "six-exercises"])
def test_live_screen_query_budget(client, user, django_assert_max_num_queries, queued):
    """Живой экран не должен зависеть от числа упражнений в очереди."""
    fill_history(user)
    active = WorkoutFactory(user=user, duration_min=None)
    for number, exercise in enumerate(ExerciseFactory.create_batch(queued), start=1):
        StrengthSetFactory(workout=active, exercise=exercise, set_number=number, done=False)

    client.force_login(user)
    with django_assert_max_num_queries(9):
        client.get(reverse("workout_live", args=[active.pk]))


@pytest.mark.parametrize("queued", [1, 6], ids=["one-exercise", "six-exercises"])
def test_live_screen_with_notes_query_budget(client, user, django_assert_max_num_queries, queued):
    """Заметки берутся одним запросом на тренировку плюс одним на прошлую заметку
    текущего упражнения — и это не зависит от числа упражнений в очереди."""
    bench = fill_history(user)
    active = WorkoutFactory(user=user, duration_min=None)
    # Текущее упражнение с историей: только тогда считается прошлая заметка.
    StrengthSetFactory(workout=active, exercise=bench, set_number=1, done=False)
    ExerciseNoteFactory(workout=active, exercise=bench, text="узкий хват")
    for number, exercise in enumerate(ExerciseFactory.create_batch(queued), start=2):
        StrengthSetFactory(workout=active, exercise=exercise, set_number=number, done=False)
        ExerciseNoteFactory(workout=active, exercise=exercise, text="заметка очереди")

    client.force_login(user)
    with django_assert_max_num_queries(11):
        client.get(reverse("workout_live", args=[active.pk]))


@pytest.mark.parametrize("queued", [1, 6], ids=["one-exercise", "six-exercises"])
def test_draft_screen_query_budget(client, user, django_assert_max_num_queries, queued):
    """Экран черновика — тот же живой экран без таймера: бюджет не выше."""
    fill_history(user)
    planned = WorkoutFactory(user=user, started_at=None, duration_min=None)
    for number, exercise in enumerate(ExerciseFactory.create_batch(queued), start=1):
        StrengthSetFactory(workout=planned, exercise=exercise, set_number=number, done=False)

    client.force_login(user)
    with django_assert_max_num_queries(9):
        client.get(reverse("workout_live", args=[planned.pk]))


@pytest.mark.parametrize("drafts", [1, 4], ids=["one-draft", "four-drafts"])
def test_start_modal_query_budget(client, user, django_assert_max_num_queries, drafts):
    """Число запросов чузера не должно расти вместе с числом черновиков.

    Черновики обоих видов: силовой подписан составом, кардио — целью по
    дистанции, и цель обязана приходить тем же запросом (select_related), иначе
    каждая строка плана спрашивала бы свою CardioPart отдельно.
    """
    fill_history(user)
    for _ in range(drafts):
        planned = WorkoutFactory(user=user, started_at=None, duration_min=None)
        StrengthSetFactory(workout=planned, set_number=1, done=False)
        cardio_plan = WorkoutFactory(
            user=user,
            started_at=None,
            duration_min=None,
            sport__category=Sport.Category.CARDIO,
        )
        CardioPartFactory(workout=cardio_plan, distance_km=30)
        # План без цели по дистанции — частей у него нет вовсе. Prefetch отдаёт
        # пустой список без отдельного запроса; этот черновик здесь ровно затем,
        # чтобы проверка не сломалась молча, если prefetch когда-нибудь уберут.
        WorkoutFactory(
            user=user,
            started_at=None,
            duration_min=None,
            sport__category=Sport.Category.CARDIO,
            target_duration_min=45,
        )

    client.force_login(user)
    # Идущая тренировка и черновики берутся одним запросом с аннотацией, седьмой —
    # подписи черновиков по группам мышц, восьмой — их кардио-части вместе с
    # видами спорта: оба агрегата один на всех, а не по строке.
    with django_assert_max_num_queries(8):
        client.get(reverse("workout_start"))


def test_profile_query_budget(client, user, django_assert_max_num_queries):
    """Профиль упёрт в потолок: 9 запросов из 9, и любой новый счётчик его сломает.

    Девятый — агрегат мест: он отдаёт и число, и название дефолта, потому что
    двумя запросами лимит был бы уже пробит.
    """
    fill_history(user)

    client.force_login(user)
    with django_assert_max_num_queries(9):
        client.get(reverse("profile"))


@pytest.mark.parametrize("places", [1, 5], ids=["one-place", "five-places"])
def test_my_locations_query_budget(client, user, django_assert_max_num_queries, places):
    """Число запросов не растёт вместе с числом мест: подписи идут одной аннотацией."""
    for number in range(places):
        place = LocationFactory(owner=user, name=f"Зал {number}")
        WorkoutFactory(user=user, location=place)

    client.force_login(user)
    with django_assert_max_num_queries(6):
        client.get(reverse("my_locations"))


@pytest.mark.parametrize("exercises", [1, 6], ids=["one-exercise", "six-exercises"])
def test_workout_summary_query_budget(client, user, django_assert_max_num_queries, exercises):
    """Итог тренировки не должен зависеть от числа упражнений в ней.

    Экран показывает номер, объём и подходы каждого упражнения — то есть ровно тот
    сорт данных, куда легко въезжает запрос на упражнение. Ценность теста не в
    самом числе, а в том, что оно одинаково при одном упражнении и при шести.
    """
    workout = WorkoutFactory(user=user, duration_min=60)
    for number in range(exercises):
        exercise = ExerciseFactory(name=f"Упражнение {number}")
        StrengthSetFactory(workout=workout, exercise=exercise, set_number=1, weight_kg=70, reps=8)

    client.force_login(user)
    # Восьмой — группы мышц для заголовка: на экране одной тренировки это один
    # запрос, зато правило подписи остаётся одно на все экраны.
    # Девятый — кардио-части вместе с их видами спорта: один запрос на блок.
    with django_assert_max_num_queries(9):
        client.get(reverse("workout_summary", args=[workout.pk]))


@pytest.mark.parametrize("weeks", [2, 8], ids=["short-history", "long-history"])
def test_history_query_budget(client, user, django_assert_max_num_queries, weeks):
    """Лента истории не должна зависеть от числа карточек на странице.

    Здесь это главное: подпись каждой силовой карточки собирается из её групп
    мышц, и наивная реализация свойством модели дала бы запрос на карточку.
    Ценность теста не в самом числе, а в том, что оно одинаково при двух неделях
    истории и при восьми. Девятый запрос — подписи всей страницы одним агрегатом.
    """
    fill_history(user, weeks=weeks)

    client.force_login(user)
    with django_assert_max_num_queries(10):
        client.get(reverse("workout_history"))


@pytest.mark.parametrize("weeks", [2, 8])
def test_export_query_budget(client, user, django_assert_max_num_queries, weeks):
    """Выгрузка: три запроса на данные, сколько бы истории ни было.

    Ценность теста не в числе, а в том, что оно одинаково при двух неделях и при
    восьми: подходы и заметки выбираются пачкой, а не по тренировке.
    """
    fill_history(user, weeks=weeks)
    ExerciseNoteFactory(workout=user.workouts.first(), exercise=ExerciseFactory())
    client.force_login(user)

    # Семь: сессия, пользователь, транзакция запроса — и три на данные.
    with django_assert_max_num_queries(8):
        response = client.get(reverse("workout_export"))
        b"".join(response.streaming_content)


def test_data_transfer_page_query_budget(client, user, django_assert_max_num_queries):
    """Страница обмена: один счётчик тренировок и ничего больше."""
    fill_history(user, weeks=4)
    client.force_login(user)

    with django_assert_max_num_queries(5):
        client.get(reverse("data_transfer"))


def own_catalog(user, count):
    """Свои упражнения с личным шагом веса — у каждого строка настроек."""
    for number in range(count):
        exercise = ExerciseFactory(owner=user, name=f"Своё {number}")
        ExerciseSettingsFactory(user=user, exercise=exercise, weight_step=Decimal("1"))


@pytest.mark.parametrize("count", [2, 12])
def test_exercise_export_query_budget(client, user, django_assert_max_num_queries, count):
    """Выгрузка справочника: два запроса на данные — упражнения со счётчиком
    тренировок и шаги веса, — сколько бы упражнений ни было."""
    own_catalog(user, count)
    client.force_login(user)

    # Шесть: сессия, пользователь, транзакция запроса — и два на данные.
    with django_assert_max_num_queries(6):
        response = client.get(reverse("exercise_export"))
        b"".join(response.streaming_content)


@pytest.mark.parametrize("count", [2, 12])
def test_unchanged_exercise_import_query_budget(client, user, django_assert_max_num_queries, count):
    """Повторная загрузка нетронутой выгрузки: строки сверяются в памяти, запись не
    идёт ни одна — поэтому число запросов одно и при двух упражнениях, и при двенадцати."""
    own_catalog(user, count)
    buffer = io.BytesIO()
    exercise_excel.build_workbook(user).save(buffer)
    upload = SimpleUploadedFile("справочник.xlsx", buffer.getvalue())
    client.force_login(user)

    # Семь: сессия, пользователь, транзакция запроса, два на справочник и шаги
    # и один — счётчик тренировок в ответной странице.
    with django_assert_max_num_queries(7):
        client.post(reverse("exercise_import"), {"exercises-file": upload})
