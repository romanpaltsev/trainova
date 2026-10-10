"""Экраны тренировок: лента истории, ввод кардио, живой режим, личные справочники.

Каждый queryset пользовательских данных фильтруется по request.user — чужая запись
по прямому URL даёт 404.
"""

import io
import math
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from operator import attrgetter
from typing import NamedTuple

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, OuterRef, Prefetch, Q, Subquery
from django.db.models.deletion import ProtectedError, RestrictedError
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.dateparse import parse_date
from django.views.generic import DeleteView, ListView, TemplateView, View

from workouts import (
    contributions,
    excel,
    excel_import,
    exercise_excel,
    request_notify,
    services,
    stats,
    trash,
)
from workouts.forms import (
    MAX_DURATION_HOURS,
    BodyMeasurementForm,
    BodyMetricForm,
    CardioPartForm,
    CardioWorkoutForm,
    CatalogRequestForm,
    ExerciseCreateForm,
    ExerciseQuickForm,
    FinishDurationForm,
    MachineCreateForm,
    MachineNameForm,
    SportForm,
    StrengthTimeForm,
    XlsxUploadForm,
)
from workouts.models import (
    BODY_METRIC_NAME_MAX_LENGTH,
    BODY_METRIC_UNIT_MAX_LENGTH,
    DEFAULT_WEIGHT_STEP,
    EQUIPMENT_MAX_LENGTH,
    EXERCISE_NAME_MAX_LENGTH,
    LOCATION_NAME_MAX_LENGTH,
    MACHINE_BRAND_MAX_LENGTH,
    MACHINE_MODEL_MAX_LENGTH,
    MAX_WEIGHT_KG,
    MEASUREMENT_FIELDS,
    METRIC_LABELS,
    METRIC_UNITS,
    MUSCLE_GROUP_MAX_LENGTH,
    NOTE_MAX_LENGTH,
    REQUIRED_FIELD,
    REST_DELTAS,
    SET_LIMITS,
    SET_STEPS,
    TIME_MEASUREMENTS,
    WEIGHT_STEP_CHOICES,
    BodyMeasurement,
    BodyMetric,
    CatalogRequest,
    ChangelogEntry,
    Exercise,
    ExerciseMachine,
    ExerciseNote,
    ExerciseSettings,
    Location,
    MachineBrand,
    MachineModel,
    Sport,
    StrengthSet,
    Workout,
    cardio_parts_prefetch,
    chosen_equipment,
    chosen_muscle_group,
    clamp_rest_seconds,
    collapse_spaces,
    decimal_display,
    exercise_usage,
    facets_for,
    is_machine,
    machine_label,
    measurement_delta,
    measurement_display,
    metric_display,
    parse_field_value,
    parse_weight_step,
    rest_display,
    ru_plural,
    suggests_machine,
    with_weight_step,
)
from workouts.stats import week_start, week_title

HISTORY_PAGE_SIZE = 10

# Шаги и границы значений подхода живут в модели (SET_STEPS, SET_LIMITS): по ним
# же подписаны кнопки и предсказывается значение на клиенте. Ключи там — поля
# модели, поэтому применимость поля к единице упражнения проверяется по
# MEASUREMENT_FIELDS без словаря-переводчика. Там же REQUIRED_FIELD — порог
# «подход выполнен», общий у живого режима, записи задним числом и импорта.
# Сколько строк показывает поиск упражнений. Полсотни — весь справочник в шесть
# десятков записей почти целиком: с точными именами («Жим штанги лёжа», «Жим
# гантелей лёжа») по запросу «жим» находится полтора десятка, и обрезка на
# тридцати прятала бы нужное.
EXERCISE_RESULTS_LIMIT = 50
# Дашборд: силовых рекордов в блоке (кардио добавляются по числу видов).
STRENGTH_RECORDS_LIMIT = 3


class WorkoutHistoryView(LoginRequiredMixin, ListView):
    """Лента тренировок: карточками, по убыванию даты, с подгрузкой по кнопке."""

    template_name = "workouts/history.html"
    context_object_name = "workouts"
    paginate_by = HISTORY_PAGE_SIZE
    extra_context = {"nav_active": "history"}

    def get_queryset(self):
        queryset = (
            # Незавершённая (живой режим) в ленту не попадает: у неё нет длительности.
            Workout.objects.filter(user=self.request.user)
            .finished()
            # cardio_parts — отдельным запросом на всю страницу: частей у
            # тренировки может быть несколько, и джойн размножил бы карточки.
            # location — для подписи места, тем же запросом.
            .select_related("sport", "location")
            .prefetch_related(cardio_parts_prefetch())
            # Иначе каждая силовая карточка делала бы свой COUNT по подходам
            .annotate(
                exercises_count=Count("sets__exercise", distinct=True),
                **stats.WORKLOAD_ANNOTATIONS,
            )
            # Сортировку задаём явно: в запросах с GROUP BY Django игнорирует
            # Meta.ordering, а пагинации нужен детерминированный порядок.
            .order_by("-started_at", "-id")
        )
        sport_id = self.request.GET.get("sport")
        if sport_id and sport_id.isdecimal():
            # Вид спорта ищем и у тренировки, и у её кардио-частей: смешанная
            # записана как силовая, но по чипу «Бег» найтись обязана. distinct —
            # из-за джойна по частям.
            queryset = queryset.filter(
                Q(sport_id=int(sport_id)) | Q(cardio_parts__sport_id=int(sport_id))
            ).distinct()
        location_id = self.request.GET.get("location")
        if location_id and location_id.isdecimal():
            # Мусор и чужой id молча дают пустую ленту: queryset уже сужен по
            # user, поэтому утечки нет, а 404 на устаревшей вкладке был бы
            # грубостью — то же решение, что у фильтра по видам спорта.
            queryset = queryset.filter(location_id=int(location_id))
        return queryset

    def get_template_names(self):
        # Подгрузка следующей страницы отдаёт только партиал со карточками.
        if self.request.headers.get("HX-Request"):
            return ["workouts/_history_page.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Подпись по группам мышц — одним запросом на страницу, до разбивки по
        # неделям: иначе каждая карточка спрашивала бы свои подходы сама.
        context["workouts"] = stats.attach_muscle_groups(self.request.user, context["workouts"])
        context["groups"] = self._group_by_week(context["workouts"])
        context["prev_week"] = self.request.GET.get("prev_week", "")
        context["last_week"] = context["groups"][-1]["key"] if context["groups"] else ""
        context["sport_filter"] = self.request.GET.get("sport", "")
        context["sports_used"] = (
            # Оба условия — в одном filter: два вызова подряд дали бы два JOIN'а,
            # то есть «есть моя тренировка И есть чья-то завершённая». Чип строится
            # только по записанным: у черновика карточек в ленте нет.
            #
            # Второе условие — виды спорта кардио-частей: бег, который был только
            # заминкой внутри силовой, тоже обязан дать чип, иначе фильтр по нему
            # недостижим.
            Sport.objects.filter(
                Q(workouts__user=self.request.user, workouts__duration_min__isnull=False)
                | Q(
                    cardio_parts__workout__user=self.request.user,
                    cardio_parts__workout__duration_min__isnull=False,
                )
            )
            .distinct()
            .order_by("name")
        )
        context["location_filter"] = self.request.GET.get("location", "")
        context["locations_used"] = (
            # Та же осторожность с одним filter, что и у sports_used выше.
            Location.objects.filter(
                workouts__user=self.request.user, workouts__duration_min__isnull=False
            )
            .distinct()
            .order_by("name")
        )
        return context

    def _group_by_week(self, workouts):
        today = timezone.localdate()
        groups = []
        for workout in workouts:
            start = week_start(timezone.localtime(workout.started_at).date())
            if not groups or groups[-1]["key"] != start.isoformat():
                groups.append(
                    {"key": start.isoformat(), "title": week_title(start, today), "items": []}
                )
            groups[-1]["items"].append(workout)
        return groups


class CardioWorkoutFormView(LoginRequiredMixin, View):
    """Создание, подготовка и правка кардио-тренировки одной формой.

    Три сценария, одна форма: записать состоявшуюся, подготовить план на потом
    (planned=True — без даты, длительности и пульса) и дозаполнить готовый план
    до записанной тренировки. Последнее отдельного кода не потребовало: черновик
    приходит сюда обычным instance, а форма в полном режиме спрашивает как раз
    то, чего у плана не было.
    """

    template_name = "workouts/cardio_form.html"
    # Ставится через as_view(planned=True) на маршруте подготовки — тем же
    # приёмом, что у StrengthWorkoutStartView.
    planned = False

    def get_instance(self):
        if "pk" not in self.kwargs:
            return None
        workout = get_object_or_404(
            Workout.objects.select_related("sport"), pk=self.kwargs["pk"], user=self.request.user
        )
        if owns_sets(workout):
            # Дом тренировки с подходами — живой режим и итог. Эта форма
            # спрашивает дату и длительность занятия целиком, и у смешанной она
            # подменила бы собой экран, где живут подходы.
            raise Http404("У тренировки с подходами свой экран")
        return workout

    def get(self, request, **kwargs):
        instance = self.get_instance()
        form = CardioWorkoutForm(
            user=request.user,
            instance=instance,
            planned=self.planned,
            initial=self.preselected(request, instance),
        )
        return self.render_form(form, instance)

    def preselected(self, request, instance):
        """Вид спорта и день из чузера «+» (?sport=, ?planned_for=): подсказка, а не адрес.

        Чужой личный, силовой или мусорный id молча игнорируем — 404 здесь был бы
        грубостью, а расширить набор сохраняемых видов параметр всё равно не может:
        форма валидирует sport своим queryset'ом. На правке подсказка запрещена:
        переданный initial перебивает данные тренировки и подменил бы ей вид спорта.

        День подставляется только у формы плана: на записи поля planned_for нет
        вовсе, и ключ в initial был бы мёртвым.
        """
        if instance is not None:
            return None
        initial = {}
        sport_id = request.GET.get("sport", "")
        if sport_id.isdecimal():
            pk = (
                Sport.objects.visible_to(request.user)
                .filter(category=Sport.Category.CARDIO, pk=int(sport_id))
                .values_list("pk", flat=True)
                .first()
            )
            if pk is not None:
                initial["sport"] = pk
        if self.planned:
            day = parse_day(request.GET.get("planned_for", ""))
            if day is not None:
                initial["planned_for"] = day
        return initial or None

    def post(self, request, **kwargs):
        instance = self.get_instance()
        # Запоминаем до сохранения: после него черновик уже перестал им быть.
        was_planned = instance is not None and instance.is_planned
        form = CardioWorkoutForm(
            request.POST, user=request.user, instance=instance, planned=self.planned
        )
        if form.is_valid():
            workout = form.save()
            if self.planned:
                # В историю плану нельзя: он туда не попадает, пока не записан.
                # Возвращаем на дашборд — там же чузер, где план и появится.
                messages.success(request, "Тренировка подготовлена.")
                return redirect("dashboard")
            messages.success(
                request,
                "Тренировка обновлена." if instance and not was_planned else "Тренировка записана.",
            )
            return redirect(reverse("workout_history") + f"#workout-{workout.pk}")
        return self.render_form(form, instance)

    def render_form(self, form, instance):
        return render(
            self.request,
            self.template_name,
            {
                "form": form,
                "workout": instance,
                # Чипы мест берём из queryset'а самой формы: тогда «что видно» и
                # «что можно сохранить» не могут разъехаться.
                "locations": form.fields["location"].queryset,
                "selected_location": form["location"].value(),
                "location_own": form["location_own"].value() or "",
                "location_max_length": LOCATION_NAME_MAX_LENGTH,
                "planned": self.planned,
                # Черновик открыт на запись: заголовок и кнопка должны говорить
                # «записать», а не «сохранить изменения» — тренировки ещё не было.
                "recording_plan": instance is not None and instance.is_planned,
                "nav_active": "history" if instance and not instance.is_planned else "add",
            },
        )


def trash_subtitle(workout):
    """Подзаголовок строки корзины: когда была тренировка — или что за черновик."""
    if workout.is_planned:
        return f"Черновик · {plan_label(workout)}"
    started = timezone.localtime(workout.started_at)
    # Год — только у прошлых лет: строка корзины и так в две строки на телефоне.
    when = formats.date_format(
        started, "j E, H:i" if started.year == timezone.localdate().year else "j E Y, H:i"
    )
    return when if workout.is_finished else f"Не завершена · начата {when}"


class WorkoutDeleteView(LoginRequiredMixin, DeleteView):
    """Удаление своей тренировки или черновика с подтверждением.

    Удаляется по-настоящему, но снимок ложится в корзину на 30 дней
    (workouts.trash): сообщение несёт кнопку «Восстановить», а «Недавно
    удалённые» — в профиле и внизу истории.
    """

    template_name = "workouts/workout_confirm_delete.html"
    context_object_name = "workout"
    # Выставляется в form_valid до удаления: после него объекта в базе уже нет.
    planned = False

    def get_queryset(self):
        return Workout.objects.filter(user=self.request.user).select_related("sport")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Та же подпись, что в ленте: по ней и опознают, какую тренировку удаляют.
        stats.attach_muscle_groups(self.request.user, [self.object])
        if self.object.is_planned:
            # У черновика нет даты, поэтому в подзаголовке — состав или цель.
            context["plan_label"] = plan_label(self.object)
        return context

    def get_success_url(self):
        # Черновика в истории нет — возвращаться туда после удаления бессмысленно.
        return reverse("dashboard") if self.planned else reverse("workout_history")

    def form_valid(self, form):
        # Запоминаем до удаления: после него объекта в базе уже нет.
        self.planned = self.object.is_planned
        stats.attach_muscle_groups(self.request.user, [self.object])
        entry = trash.move_to_trash(
            self.object,
            title=self.object.muscle_groups or self.object.sport.name,
            subtitle=trash_subtitle(self.object),
        )
        # Текст простой — кнопку «Восстановить» рисует base.html по метке.
        messages.success(
            self.request,
            "Черновик удалён." if self.planned else "Тренировка удалена.",
            extra_tags=trash.restore_tag(entry),
        )
        return redirect(self.get_success_url())


class WorkoutTrashView(LoginRequiredMixin, TemplateView):
    """«Недавно удалённые»: тренировки за 30 дней с кнопкой «Восстановить»."""

    template_name = "workouts/trash.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        entries = list(trash.recent_for(self.request.user))
        for entry in entries:
            deleted_on = timezone.localtime(entry.deleted_at).date()
            left = max((timezone.localtime(entry.expires_at).date() - today).days, 0)
            entry.deleted_label = measure_day_label(deleted_on, today)
            entry.left_label = (
                f"ещё {left} {ru_plural(left, 'день', 'дня', 'дней')}" if left else "последний день"
            )
        context.update({"entries": entries, "nav_active": "profile"})
        return context


class WorkoutRestoreView(LoginRequiredMixin, View):
    """Вернуть тренировку из корзины (только POST): из сообщения или со страницы.

    Чужой или уже возвращённый снимок — 404, как у любых чужих данных.
    """

    def post(self, request, pk):
        try:
            workout = trash.restore(pk, request.user)
        except trash.RestoreRefused as refusal:
            messages.error(request, str(refusal))
            return redirect("workout_trash")
        if workout is None:
            raise Http404
        messages.success(
            request, "Черновик восстановлен." if workout.is_planned else "Тренировка восстановлена."
        )
        return redirect(trash.restored_url(workout))


# ---------- Живой режим силовой тренировки ----------


def exercises_label(count):
    """«3 упражнения» для строк чузера и страницы удаления; «пусто» — если ничего нет.

    У черновика нет даты, поэтому состав — единственный способ отличить его
    от другого черновика того же вида спорта.
    """
    if not count:
        return "пусто"
    word = ru_plural(count, "упражнение", "упражнения", "упражнений")
    return f"{count} {word}"


def plan_day_label(day, today):
    """День плана коротко: «сегодня», «завтра», «сб» на ближайшую неделю, «5 сен» дальше.

    Тот же приём, что у stats.workout_row для прошедших дней, только повёрнутый
    вперёд. Коротко — не из эстетики: в строке чузера рядом стоят ещё две цели,
    а всей ширины там 262 пикселя на 375px, и «сб, 5 сен · 30 км · 1:20» в них
    уже не помещается (замерено). Прошедший день падает в «5 сен» — план, до
    которого не дошли, честнее показать датой.
    """
    if day == today:
        return "сегодня"
    if day == today + timedelta(days=1):
        return "завтра"
    if today < day <= today + timedelta(days=6):
        return formats.date_format(day, "D").lower()
    return formats.date_format(day, "j b")


def plan_label(workout, exercises_count=None):
    """Чем один черновик отличается от другого того же вида спорта.

    Сначала плановый день, если задан, потом содержание: состав упражнений и
    цели кардио-частей. Ветвления по виду спорта тут нет — у смешанного
    черновика есть и то, и другое. Все цели необязательные, поэтому «пусто» —
    законный ответ. Счётчик упражнений принимается аргументом: в чузере он
    приходит аннотацией на всю выборку сразу.
    """
    parts = []
    if workout.planned_for:
        parts.append(plan_day_label(workout.planned_for, timezone.localdate()))
    if exercises_count is None:
        exercises_count = workout.sets.values("exercise").distinct().count()
    # Содержание собирается из того, что в черновике есть: состав, цели по
    # дистанции, цель по времени. У смешанного будет и первое, и второе.
    content = []
    if exercises_count:
        content.append(exercises_label(exercises_count))
    # all() по prefetch'у: частей нет — значит цели по дистанции не задавали.
    content += [f"{part.distance_display} км" for part in workout.cardio_parts.all()]
    if workout.target_duration_min:
        content.append(workout.target_duration_display)
    # «Пусто» — только когда содержания нет вовсе: наготовить планов пачкой и
    # заполнить по дороге законно.
    parts.extend(content or ["пусто"])
    return " · ".join(parts)


def live_workout_or_404(request, pk):
    """Своя незавершённая силовая — база всех действий живого режима.

    Черновик правится тем же набором эндпоинтов, что и идущая тренировка:
    подготовка — это и есть добавление упражнений и правка весов.
    """
    return get_object_or_404(
        Workout.objects.filter(STRENGTH_WORKOUT, user=request.user)
        .unfinished()
        # user — для отдыха по умолчанию и подсказок, иначе он тянется отдельным
        # запросом; location — для строки места в шапке, тем же запросом
        .select_related("sport", "user", "location")
        # Условие по подходам джойнит sets — без distinct тренировка с тремя
        # подходами пришла бы тремя строками.
        .distinct(),
        pk=pk,
    )


def editable_workout_or_404(request, pk):
    """Своя силовая в любом состоянии — база правки её состава.

    Черновик и идущая правятся в живом режиме, записанная — на экране правки
    (WorkoutCorrectView), а эндпоинты у них общие: добавить упражнение, подход,
    заметку. Второго способа набирать подходы так и не появляется. Действия,
    которым нужно идущее время (выполнить подход, отдых, текущее упражнение,
    старт), остаются на live_workout_or_404.
    """
    return get_object_or_404(
        Workout.objects.filter(STRENGTH_WORKOUT, user=request.user)
        .select_related("sport", "user", "location")
        .distinct(),
        pk=pk,
    )


def editable_set_or_404(request, pk, *, for_update=False):
    """Подход, значения которого можно править: плановый у незавершённой
    тренировки или любой у записанной.

    Выполненный подход идущей тренировки сюда не попадает: его сначала
    возвращают в работу (SetUndoView), иначе степпер переписал бы факт под
    тикающими часами. У записанной выполнены все подходы, и правка — это и есть
    исправление факта.
    """
    editable = Q(workout__duration_min__isnull=True, done=False) | Q(
        workout__duration_min__isnull=False
    )
    queryset = with_weight_step(
        StrengthSet.objects.filter(editable, workout__user=request.user).select_related(
            "workout", "exercise"
        ),
        request.user.pk,
    )
    if for_update:
        # Тот же приём, что в live_set_or_404: быстрые тапы сериализуются на строке.
        queryset = queryset.select_for_update(of=("self",))
    return get_object_or_404(queryset, pk=pk)


def correct_context(workout, *, open_set_id=None):
    """Контекст региона правки записанной тренировки: группы и раскрытый подход."""
    groups = services.exercise_groups(workout)
    for group in groups:
        group["total"] = services.exercise_total(group["sets"])
    return {
        "workout": workout,
        "groups": groups,
        # Блоки — те же группы, где круг собран в одну обёртку (запросов нет).
        "blocks": services.blocks(groups),
        "open_set_id": open_set_id,
        # Последний подход записанной тренировки не убирается: пустая силовая
        # перестала бы быть силовой. Убрать всё — это «Удалить тренировку».
        "sets_count": sum(len(group["sets"]) for group in groups),
    }


def live_set_or_404(request, pk, *, undone_only=False, for_update=False, started_only=False):
    """Свой подход своей незавершённой тренировки; подходы завершённых неизменяемы."""
    queryset = with_weight_step(
        StrengthSet.objects.filter(
            workout__user=request.user, workout__duration_min__isnull=True
        ).select_related("workout", "exercise"),
        request.user.pk,
    )
    if started_only:
        # «Подход выполнен» до старта невозможно: в черновике время ещё не идёт.
        queryset = queryset.filter(workout__started_at__isnull=False)
    if undone_only:
        queryset = queryset.filter(done=False)
    if for_update:
        # Быстрые тапы степперов сериализуются на строке: каждый шаг применяется
        # к свежему значению, N тапов = N шагов независимо от порядка прихода.
        # of=("self",) — иначе блокировались бы и присоединённые workout/exercise,
        # включая глобальные упражнения, общие для всех пользователей.
        queryset = queryset.select_for_update(of=("self",))
    return get_object_or_404(queryset, pk=pk)


def live_rest_context(workout, *, autostart=False, stop=False, oob=False):
    seconds = workout.effective_rest_seconds
    return {
        "workout": workout,
        "rest_seconds": seconds,
        "rest_display": rest_display(seconds),
        "autostart": autostart,
        # Посреди раунда круга: отдыха нет, тикающий отсчёт гасится (live.js).
        "stop": stop,
        "oob": oob,
    }


def live_region_response(
    request,
    workout,
    *,
    oob=False,
    restart_timer=False,
    stop_timer=False,
    error=None,
    open_set_id=None,
):
    """Регион упражнений; при выполненном подходе — плюс OOB-карточка отдыха.

    Карточка отдыха пересоздаётся только здесь: пересоздание перезапускает
    Alpine-таймер, поэтому обычные действия региона её не трогают.

    У записанной тренировки регион свой — экрана правки: эндпоинты общие, а
    отвечать они обязаны тем экраном, с которого их позвали.
    """
    if workout.is_finished:
        context = correct_context(workout, open_set_id=open_set_id) | {"oob": oob, "error": error}
        return HttpResponse(
            render_to_string("workouts/_correct_exercises.html", context, request=request)
        )
    context = services.live_context(workout, open_set_id) | {"oob": oob, "error": error}
    html = render_to_string("workouts/_live_exercises.html", context, request=request)
    if restart_timer or stop_timer:
        html += render_to_string(
            "workouts/_live_rest.html",
            live_rest_context(workout, autostart=restart_timer, stop=stop_timer, oob=True),
            request=request,
        )
    return HttpResponse(html)


def owns_sets(workout):
    """Есть ли у тренировки силовая часть — то есть хотя бы один подход.

    Дискриминатор навигации: экран тренировки выбирается содержимым, а не
    категорией её вида спорта. С подходами дом тренировки — живой режим и итог,
    без них — форма кардио. У смешанной подходы есть, и ответ однозначен.

    Запросом, а не свойством модели: свойство читало бы подходы у каждой
    карточки ленты и делало бы её N+1 — урок muscle_groups. Там, где строки уже
    выбраны с аннотацией exercises_count, надо смотреть на неё.
    """
    return workout.sets.exists()


# Силовая по виду спорта ИЛИ по содержимому: у смешанной тренировки вид спорта
# силовой, но и у кардио-тренировки, в которую дописали подходы, живой режим
# обязан работать.
STRENGTH_WORKOUT = Q(sport__category=Sport.Category.STRENGTH) | Q(sets__isnull=False)


def parse_day(raw):
    """День из недоверенного ввода: пусто и мусор одинаково значат «дня нет».

    parse_date сам по себе не годится: на «2026-02-30» он не возвращает None, а
    бросает ValueError — формат-то ISO, — и устаревшая вкладка или подобранный
    руками адрес роняли бы страницу пятисоткой. Один разбор на все три точки
    входа дня: чузер, модалка черновика и подсказка кардио-плана.
    """
    try:
        return parse_date(raw) if raw else None
    except ValueError:
        return None


def parse_period(params, today):
    """Окно сводки дашборда из адреса: ?period=30 или свой период ?from=…&to=….

    Мусор молча даёт 7 дней — как неизвестный фильтр истории: устаревшая
    ссылка не должна ронять главную. Свой период прощает перепутанные даты
    (меняет местами), будущее обрезает сегодняшним днём (будущих тренировок не
    бывает), а начало раньше EARLIEST_DAY поднимает до него.
    """
    first, last = parse_day(params.get("from")), parse_day(params.get("to"))
    if first and last:
        first, last = sorted((first, last))
        first, last = max(first, stats.EARLIEST_DAY), min(last, today)
        if first <= last:
            return stats.custom_period(first, last)
    key = params.get("period")
    return stats.preset_period(key if key in stats.PERIOD_PRESETS else stats.DEFAULT_PERIOD, today)


def period_chips(period):
    """Чипы готовых окон. У окна по умолчанию адрес без параметра — чистая главная."""
    dashboard = reverse("dashboard")
    return [
        {
            "label": chip,
            "url": dashboard if key == stats.DEFAULT_PERIOD else f"{dashboard}?period={key}",
            "active": key == period.key,
        }
        for key, (chip, *_rest) in stats.PERIOD_PRESETS.items()
    ]


def unfinished_workouts(user):
    """Идущая тренировка и подготовленные черновики, готовые к показу.

    Один запрос на обе сущности: вместе их единицы, а состояние выводится из
    двух колонок уже в Python. Черновики возвращаются с проставленными
    `plan_label` и подписью по группам мышц — то есть пригодными для шаблона.

    Общий хелпер на чузер «+» и блок «Подготовлено» на дашборде: разъехавшись,
    два экрана показали бы один и тот же план по-разному.
    """
    rows = list(
        Workout.objects.filter(user=user)
        .unfinished()
        # cardio_parts — ярлык кардио-черновика это его цель, и без prefetch
        # каждая строка спрашивала бы её отдельно.
        .select_related("sport")
        .prefetch_related(cardio_parts_prefetch())
        .annotate(exercises_count=Count("sets__exercise", distinct=True))
        # Явно: с GROUP BY Django игнорирует Meta.ordering. Датированные планы
        # идут по возрастанию дня — ближайший сверху, — недатированные после
        # них: nulls_last, иначе Postgres в ASC поставил бы NULL в конец сам,
        # но полагаться на это молча не стоит.
        .order_by(F("planned_for").asc(nulls_last=True), "-id")
    )
    live = next((row for row in rows if not row.is_planned), None)
    drafts = [row for row in rows if row.is_planned]
    # Черновики плана на неделю различаются только составом, поэтому им тоже
    # нужна подпись по группам мышц: у всех троих иначе было бы «Силовая».
    stats.attach_muscle_groups(user, drafts)
    for draft in drafts:
        draft.plan_label = plan_label(draft, draft.exercises_count)
    return live, drafts


class WorkoutStartView(LoginRequiredMixin, View):
    """HTMX-модалка «+»: продолжить идущую, открыть черновик, начать или записать."""

    def get(self, request):
        live, drafts = unfinished_workouts(request.user)
        return render(
            request,
            "workouts/_start_modal.html",
            {
                "live": live,
                "drafts": drafts,
                # Начать вторую идущую нельзя, а подготовить следующую — можно,
                # поэтому список видов спорта больше не сужается.
                "can_start_now": live is None,
                # Силовые сверху, дальше по алфавиту — тот же порядок, что у легенды
                # графика дашборда: главное действие оказывается первым в списке.
                "sports": sorted(
                    Sport.objects.visible_to(request.user),
                    key=lambda sport: (not sport.is_strength, sport.name),
                ),
            },
        )


class StrengthWorkoutStartView(LoginRequiredMixin, View):
    """Силовая из чузера: сразу в живой режим либо черновиком (planned=True)."""

    # Ставится через as_view(planned=True) на маршруте подготовки.
    planned = False

    def post(self, request):
        sport_id = request.POST.get("sport", "")
        if not sport_id.isdecimal():
            raise Http404("Вид спорта не указан")
        sport = get_object_or_404(
            Sport.objects.visible_to(request.user).filter(category=Sport.Category.STRENGTH),
            pk=int(sport_id),
        )
        # Место силовая берёт молча: в зале лишний шаг не нужен, а сменить его
        # можно на самом экране тренировки. Один запрос на обе ветки.
        location = Location.objects.default_for(request.user)
        if self.planned:
            # День приходит из чузера. Читаем его ТОЛЬКО в этой ветке: у начатой
            # тренировки планового дня не бывает (planned_for_only_when_planned),
            # а скрытое поле остаётся в разметке и в режиме «начать сейчас» —
            # Alpine подменяет там лишь адрес. Мусор уводит в пустоту, а не в
            # 500: тот же выбор, что у WorkoutPlannedForView.
            raw = request.POST.get("planned_for", "")
            # Черновиков может быть сколько угодно: уникальный индекс требует начала,
            # поэтому ловить IntegrityError здесь не нужно.
            workout = Workout.objects.create(
                user=request.user,
                sport=sport,
                location=location,
                started_at=None,
                duration_min=None,
                planned_for=parse_day(raw),
            )
            return redirect("workout_live", pk=workout.pk)
        try:
            # Вложенный atomic: гонку двух вкладок ловит частичный уникальный индекс,
            # а savepoint не даёт IntegrityError отравить транзакцию запроса.
            with transaction.atomic():
                workout = Workout.objects.create(
                    user=request.user,
                    sport=sport,
                    location=location,
                    started_at=timezone.now(),
                    duration_min=None,
                )
        except IntegrityError:
            workout = Workout.objects.filter(user=request.user).live().first()
            if workout is None:
                raise
        return redirect("workout_live", pk=workout.pk)


class WorkoutDraftStartView(LoginRequiredMixin, View):
    """«Начать тренировку»: с этого момента идут часы черновика."""

    def post(self, request, pk):
        workout = live_workout_or_404(request, pk)
        if not workout.is_planned:
            # Даблтап или кнопка «назад»: тренировка уже идёт.
            return redirect("workout_live", pk=workout.pk)
        live = Workout.objects.filter(user=request.user).live().first()
        if live is not None:
            messages.info(request, "Сначала завершите текущую тренировку.")
            return redirect("workout_live", pk=live.pk)
        try:
            # До UPDATE черновика в частичном индексе нет, после — есть: гонку
            # «две вкладки стартуют разные черновики» ловит база, а savepoint не
            # даёт IntegrityError отравить транзакцию запроса (ATOMIC_REQUESTS).
            # Условие в filter, а не присваивание полю: при двойном тапе Postgres
            # перепроверит started_at IS NULL уже под блокировкой строки, и время
            # начала останется от первого нажатия, а не сдвинется назад.
            with transaction.atomic():
                # planned_for обнуляется здесь же: он бывает только у черновика
                # (констрейнт planned_for_only_when_planned), а с этого UPDATE
                # тренировка перестаёт им быть. Отдельным запросом нельзя —
                # между ними строка нарушала бы констрейнт.
                Workout.objects.filter(pk=workout.pk, started_at__isnull=True).update(
                    started_at=timezone.now(), planned_for=None
                )
        except IntegrityError:
            live = Workout.objects.filter(user=request.user).live().first()
            if live is None:
                raise
            messages.info(request, "Сначала завершите текущую тренировку.")
            return redirect("workout_live", pk=live.pk)
        return redirect("workout_live", pk=workout.pk)


class LiveWorkoutView(LoginRequiredMixin, View):
    """Экран живого режима силовой тренировки.

    ?set=<id> открывает степперы планового подхода текущего упражнения —
    приём экрана правки: тап по строке плана и «Готово» запрашивают регион
    упражнений (HTMX), а не страницу. Чужой, выполненный или устаревший id
    ничего не ломает: открывается текущий подход.
    """

    def get(self, request, pk):
        workout = get_object_or_404(
            Workout.objects.filter(user=request.user)
            .select_related("sport", "user", "location")
            # Кардио-части блока «Кардио» — одним запросом вместе с их видами
            # спорта, иначе каждая строка спрашивала бы свой.
            .prefetch_related(cardio_parts_prefetch()),
            pk=pk,
        )
        if not workout.sport.is_strength and not owns_sets(workout):
            raise Http404("Живой режим есть только у силовых тренировок")
        if workout.is_finished:
            summary = reverse("workout_summary", args=[workout.pk])
            if request.headers.get("HX-Request"):
                # Устаревшая вкладка: обычный 302 htmx прошёл бы сам и вставил
                # страницу итога внутрь региона упражнений.
                return HttpResponse(headers={"HX-Redirect": summary})
            return redirect(summary)
        raw = request.GET.get("set", "")
        context = services.live_context(workout, int(raw) if raw.isdecimal() else None)
        if request.headers.get("HX-Request"):
            return render(request, "workouts/_live_exercises.html", context)
        return render(request, "workouts/live.html", context | live_rest_context(workout))


class LiveExerciseView(LoginRequiredMixin, View):
    """Модалка «+ Упражнение»: поиск по видимым упражнениям и быстрое создание."""

    def get(self, request, pk):
        workout = editable_workout_or_404(request, pk)
        context = self.search_context(request, workout)
        if "q" in request.GET:
            return render(request, "workouts/_exercise_results.html", context)
        return render(request, "workouts/_exercise_modal.html", context)

    def post(self, request, pk):
        workout = editable_workout_or_404(request, pk)
        exercise_id = request.POST.get("exercise", "")
        if exercise_id:
            if not exercise_id.isdecimal():
                raise Http404("Упражнение не найдено")
            # Священное правило: чужое личное упражнение по прямому id — 404.
            exercise = get_object_or_404(
                Exercise.objects.visible_to(request.user), pk=int(exercise_id)
            )
        else:
            form = ExerciseQuickForm(request.POST, user=request.user)
            if not form.is_valid():
                context = self.search_context(request, workout, form=form)
                return render(request, "workouts/_exercise_modal.html", context)
            exercise = form.save_for_user()

        if not workout.sets.filter(exercise=exercise).exists():
            # «+ Упражнение в суперсет»: номер проверяется — у разобранного круга
            # упражнение просто встаёт отдельно.
            circuit = services.valid_circuit(workout, request.POST.get("circuit"))
            try:
                with transaction.atomic():
                    # В записанной тренировке подходы сразу выполненные: в ней
                    # плановых не бывает (их удаляет завершение).
                    services.create_planned_sets(
                        workout, exercise, done=workout.is_finished, circuit=circuit
                    )
            except IntegrityError:
                pass  # даблтап: упражнение уже добавил параллельный запрос
        # Пустое тело закрывает модалку, регион упражнений обновляется out-of-band —
        # тот же приём, что в SportCreateView.
        return live_region_response(request, workout, oob=True)

    def search_context(self, request, workout, form=None):
        query = (request.GET.get("q") or request.POST.get("name") or "").strip()
        exercises = (
            Exercise.objects.visible_to(request.user)
            .exclude(pk__in=workout.sets.values("exercise"))
            .order_by("name")
        )
        if query:
            exercises = exercises.filter(name__icontains=query)
        offer_create = (
            bool(query)
            and not Exercise.objects.visible_to(request.user).filter(name__iexact=query).exists()
        )
        # Выбранная единица возвращается на круг по той же причине, что группа
        # и снаряд (см. chosen_facet_value): чипы живут в свапаемом блоке.
        chosen = request.GET.get("measurement") or request.POST.get("measurement") or ""
        group = chosen_facet_value(request, "muscle_group")
        equipment = chosen_facet_value(request, "equipment")
        # Лишняя строка — весь способ узнать, что список обрезан: COUNT(*) стоил
        # бы второго запроса на каждую набранную букву.
        found = list(exercises[: EXERCISE_RESULTS_LIMIT + 1])
        facets = facets_for(request.user) if offer_create else None
        return {
            "workout": workout,
            # Номер круга едет через поиск и создание скрытым полем результатов.
            "circuit": services.valid_circuit(
                workout, request.GET.get("circuit") or request.POST.get("circuit")
            ),
            "exercises": found[:EXERCISE_RESULTS_LIMIT],
            "results_truncated": len(found) > EXERCISE_RESULTS_LIMIT,
            "q": query,
            "offer_create": offer_create,
            "form": form,
            "measurement_choices": Exercise.Measurement.choices,
            "selected_measurement": (
                chosen
                if chosen in Exercise.Measurement.values
                else Exercise.Measurement.WEIGHT_REPS
            ),
            # Только при предложении создать: на поиске списки фасетов не нужны,
            # и лишний запрос на каждую набранную букву тоже.
            "muscle_groups": facets.muscle_groups if facets else [],
            "selected_muscle_group": group.strip(),
            "muscle_group_max_length": MUSCLE_GROUP_MAX_LENGTH,
            "equipment_list": facets.equipment if facets else [],
            "selected_equipment": equipment.strip(),
            "equipment_max_length": EQUIPMENT_MAX_LENGTH,
        }


def chosen_facet_value(request, field):
    """Значение фасета, как его прислала форма поиска или создания.

    Выбор возвращается на круг: чипы живут в свапаемом блоке результатов, и без
    этого следующая набранная буква сбросила бы и группу, и снаряд.
    """
    return (
        request.GET.get(f"{field}_own")
        or request.GET.get(field)
        or request.POST.get(f"{field}_own")
        or request.POST.get(field)
        or ""
    )


class LiveExerciseSelectView(LoginRequiredMixin, View):
    """Тап по строке очереди — переключить текущее упражнение."""

    def post(self, request, pk):
        workout = live_workout_or_404(request, pk)
        exercise_id = request.POST.get("exercise", "")
        if not exercise_id.isdecimal():
            raise Http404("Упражнение не найдено")
        exercise = get_object_or_404(
            Exercise.objects.filter(sets__workout=workout, sets__done=False).distinct(),
            pk=int(exercise_id),
        )
        workout.current_exercise = exercise
        workout.save(update_fields=["current_exercise"])
        return live_region_response(request, workout)


class LiveSetAddView(LoginRequiredMixin, View):
    """«+ Добавить подход»: новый подход повторяет предыдущий — типичный кейс в зале."""

    def post(self, request, pk):
        workout = editable_workout_or_404(request, pk)
        exercise_id = request.POST.get("exercise", "")
        if not exercise_id.isdecimal():
            raise Http404("Упражнение не найдено")
        exercise = get_object_or_404(
            Exercise.objects.filter(sets__workout=workout).distinct(), pk=int(exercise_id)
        )
        last = workout.sets.filter(exercise=exercise).order_by("-set_number").first()
        created = None
        try:
            with transaction.atomic():
                created = StrengthSet.objects.create(
                    workout=workout,
                    exercise=exercise,
                    set_number=last.set_number + 1 if last else 1,
                    measurement=exercise.measurement,
                    # В записанной тренировке подход сразу выполненный, а done_at
                    # пуст: когда его сделали, неизвестно — как у тренировки,
                    # внесённой задним числом.
                    done=workout.is_finished,
                    # Подход упражнения из круга — тоже в круге: номер у всех
                    # подходов упражнения один.
                    circuit=last.circuit if last else None,
                    **services.set_values(exercise.measurement, last),
                )
        except IntegrityError:
            pass  # даблтап — второй подход не нужен
        if not workout.is_finished:
            # Подход добавляют, чтобы сделать его сейчас: выполненное упражнение
            # возвращается в «Сейчас», как после «вернуть подход в работу».
            workout.current_exercise = exercise
            workout.save(update_fields=["current_exercise"])
        # Новый подход сразу раскрыт — и на экране правки, и в живом режиме: его
        # добавили, чтобы поправить, а панель на первом подходе плана уводила
        # правку не туда. Даблтап (created is None) открывает текущий.
        return live_region_response(request, workout, open_set_id=created.pk if created else None)


def circuit_modal_response(request, workout, *, refresh=False):
    """Окно «Суперсеты»; после правки — плюс регион упражнений out-of-band.

    Между соседними блоками — «связать»: последний член предыдущего с первым
    следующего. Пары считаются здесь, а не в шаблоне: шаблону соседей не видно.
    """
    all_blocks = services.blocks(services.exercise_groups(workout))
    items = []
    for index, block in enumerate(all_blocks):
        if index:
            items.append(
                {
                    "kind": "link",
                    "exercise": all_blocks[index - 1]["members"][-1]["exercise"].pk,
                    "next": block["members"][0]["exercise"].pk,
                }
            )
        items.append({"kind": "block", "block": block})
    html = render_to_string(
        "workouts/_circuit_modal.html",
        # Связывать есть что, пока в тренировке два упражнения и больше —
        # даже если все они уже в одном круге (тогда связок просто нет).
        {
            "workout": workout,
            "items": items,
            "can_link": sum(len(block["members"]) for block in all_blocks) > 1,
        },
        request=request,
    )
    if refresh:
        html += live_region_response(request, workout, oob=True).content.decode()
    return HttpResponse(html)


class LiveCircuitView(LoginRequiredMixin, View):
    """Окно «Суперсеты»: связать соседние упражнения в круг или убрать из него.

    Работает в идущей, в черновике и на правке записанной тренировки — круг
    можно отметить и после. «Связать» шлёт оба id: устаревшая вкладка с уже
    не соседней парой ничего не меняет, окно перерисуется с настоящим порядком.
    Упражнение не из этой тренировки — 404, как любые чужие данные.
    """

    def get(self, request, pk):
        return circuit_modal_response(request, editable_workout_or_404(request, pk))

    def post(self, request, pk):
        workout = editable_workout_or_404(request, pk)
        mine = set(workout.sets.values_list("exercise_id", flat=True))
        ids = [request.POST.get("exercise", ""), request.POST.get("next", "")]
        action = request.POST.get("action")
        if action == "link":
            if not all(raw.isdecimal() and int(raw) in mine for raw in ids):
                raise Http404("Упражнение не найдено")
            services.link_with_next(workout, int(ids[0]), int(ids[1]))
        elif action == "unlink":
            if not (ids[0].isdecimal() and int(ids[0]) in mine):
                raise Http404("Упражнение не найдено")
            services.unlink(workout, int(ids[0]))
        else:
            return HttpResponseBadRequest("Неизвестное действие")
        return circuit_modal_response(request, workout, refresh=True)


class LiveRoundAddView(LoginRequiredMixin, View):
    """«+ Подход суперсета»: ещё по подходу каждому упражнению круга."""

    def post(self, request, pk):
        workout = editable_workout_or_404(request, pk)
        circuit = services.valid_circuit(workout, request.POST.get("circuit"))
        if circuit is None:
            raise Http404("Суперсета нет")
        try:
            with transaction.atomic():
                services.add_round(workout, circuit)
        except IntegrityError:
            pass  # даблтап — второй раунд не нужен
        return live_region_response(request, workout)


class ExerciseNoteView(LoginRequiredMixin, View):
    """Заметка к упражнению тренировки: модалка на GET, сохранение на POST.

    Упражнение резолвится ЧЕРЕЗ тренировку, а тренировка — через
    editable_workout_or_404, поэтому чужая тренировка и упражнение не из этой
    тренировки дают 404 без отдельных проверок. Записанная правится тоже — на
    экране правки: «болело плечо» вспоминают и после тренировки.

    Формы здесь нет намеренно: пустой текст означает «убрать заметку», а
    ModelForm на непустом поле счёл бы это ошибкой. Остальные эндпоинты живого
    режима тоже читают POST напрямую и отдают error в шаблон.
    """

    def get(self, request, pk):
        workout, exercise = self.resolve(request, pk, request.GET.get("exercise", ""))
        return self.render_modal(request, workout, exercise, self.text(workout, exercise))

    def post(self, request, pk):
        workout, exercise = self.resolve(request, pk, request.POST.get("exercise", ""))
        text = request.POST.get("text", "").strip()
        if len(text) > NOTE_MAX_LENGTH:
            return self.render_modal(
                request, workout, exercise, text, error="Заметка слишком длинная."
            )
        if text:
            # update_or_create безопасен под ATOMIC_REQUESTS: внутри он свой
            # savepoint, поэтому гонка двух вкладок не отравит транзакцию.
            ExerciseNote.objects.update_or_create(
                workout=workout, exercise=exercise, defaults={"text": text}
            )
        else:
            ExerciseNote.objects.filter(workout=workout, exercise=exercise).delete()
        # Пустое тело закрывает модалку, регион упражнений обновляется out-of-band.
        # restart_timer не передаём: сохранение заметки не должно перезапускать отдых.
        return live_region_response(request, workout, oob=True)

    @staticmethod
    def resolve(request, pk, exercise_id):
        workout = editable_workout_or_404(request, pk)
        if not exercise_id.isdecimal():
            raise Http404("Упражнение не найдено")
        # Через подходы тренировки: это строже, чем visible_to — чужое личное
        # упражнение сюда не попадёт по определению.
        exercise = get_object_or_404(
            Exercise.objects.filter(sets__workout=workout).distinct(), pk=int(exercise_id)
        )
        return workout, exercise

    @staticmethod
    def text(workout, exercise):
        return (
            ExerciseNote.objects.filter(workout=workout, exercise=exercise)
            .values_list("text", flat=True)
            .first()
            or ""
        )

    @staticmethod
    def render_modal(request, workout, exercise, text, error=""):
        return render(
            request,
            "workouts/_note_modal.html",
            {
                "workout": workout,
                "exercise": exercise,
                "text": text,
                "max_length": NOTE_MAX_LENGTH,
                "error": error,
            },
        )


class LiveRestView(LoginRequiredMixin, View):
    """Сохранение длительности отдыха тренировки (кнопки ±15 на нетикающем таймере)."""

    def post(self, request, pk):
        workout = live_workout_or_404(request, pk)
        delta = request.POST.get("delta", "")
        if delta not in REST_DELTAS:
            return HttpResponseBadRequest("Недопустимый шаг")
        workout.rest_seconds = clamp_rest_seconds(workout.effective_rest_seconds + int(delta))
        workout.save(update_fields=["rest_seconds"])
        # Клиент уже обновился оптимистично; 204 без свапа не трогает таймер.
        return HttpResponse(status=204)


def save_set_field(request, row, field, value):
    """Сохранить поле подхода; ответ — его значение для степпера.

    Меняется только этот подход: соседние плановые не трогаются, даже если у
    них был тот же вес, — правка одного числа не должна молча переписывать
    другие.
    """
    setattr(row, field, value)
    row.save(update_fields=[field])
    return HttpResponse(row.field_display(field))


class SetAdjustView(LoginRequiredMixin, View):
    """Степперы веса, повторов и времени: каждый тап сохраняется сразу."""

    def post(self, request, pk):
        field = request.POST.get("field", "")
        direction = request.POST.get("dir", "")
        if field not in SET_STEPS or direction not in {"up", "down"}:
            return HttpResponseBadRequest("Недопустимый шаг")
        row = editable_set_or_404(request, pk, for_update=True)
        if field not in MEASUREMENT_FIELDS[row.measurement]:
            # Вес у планки писать нельзя: подход упёрся бы в ограничение
            # set_fields_match_measurement, а это 500 вместо внятного отказа.
            return HttpResponseBadRequest("Поле не подходит единице упражнения")
        # Вес шагает по настройке упражнения, остальные поля — по общему шагу.
        step = row.effective_weight_step if field == "weight_kg" else SET_STEPS[field]
        if direction == "down":
            step = -step
        if field == "weight_kg":
            value = min(MAX_WEIGHT_KG, max(Decimal(0), Decimal(str(row.weight_kg)) + step))
        else:
            value = min(SET_LIMITS[field], max(0, getattr(row, field) + step))
        return save_set_field(request, row, field, value)


class SetValueView(LoginRequiredMixin, View):
    """Ввод значения подхода руками: тап по числу вместо серии тапов по «+».

    Абсолютное значение, а не шаг: человек написал ровно то, что хотел, и
    сорока тапов по «+» для сотни килограммов больше не нужно.
    """

    def post(self, request, pk):
        field = request.POST.get("field", "")
        if field not in SET_STEPS:
            return HttpResponseBadRequest("Недопустимое поле")
        row = editable_set_or_404(request, pk, for_update=True)
        if field not in MEASUREMENT_FIELDS[row.measurement]:
            return HttpResponseBadRequest("Поле не подходит единице упражнения")
        try:
            value = parse_field_value(field, request.POST.get("value", ""))
        except ValueError as error:
            # 400 с человеческим текстом: его показывает клиент рядом со полем.
            return HttpResponseBadRequest(str(error))
        return save_set_field(request, row, field, value)


class SetDoneView(LoginRequiredMixin, View):
    """«Подход выполнен»: фиксирует подход и перезапускает таймер отдыха."""

    def post(self, request, pk):
        # started_only: в черновике этой кнопки нет, но устаревшая вкладка есть всегда.
        row = live_set_or_404(request, pk, for_update=True, started_only=True)
        if row.done:
            # Даблтап: подход уже записан, отдых перезапускать нельзя.
            return live_region_response(request, row.workout)
        field, message = REQUIRED_FIELD[row.measurement]
        if getattr(row, field) < 1:
            return live_region_response(request, row.workout, error=message)
        row.done = True
        # Метка ставится ровно здесь: по ней считается фактический порядок
        # упражнений в тренировке. Часы приложения, а не БД: под ATOMIC_REQUESTS
        # NOW() в Postgres дал бы время начала транзакции, а не отметки.
        # Даблтап метку не перезапишет — строка взята select_for_update, и второй
        # запрос выходит выше на охраннике row.done.
        row.done_at = timezone.now()
        row.save(update_fields=["done", "done_at"])
        # В круге отдых — только после раунда, и текущим становится следующий
        # член круга (A1 → B1 → A2); вне круга — прежнее поведение.
        restart, stop = services.advance_circuit(row.workout, row)
        return live_region_response(request, row.workout, restart_timer=restart, stop_timer=stop)


class SetUndoView(LoginRequiredMixin, View):
    """Тап по выполненному подходу — вернуть его в работу, значения сохраняются."""

    def post(self, request, pk):
        row = live_set_or_404(request, pk, for_update=True)
        if row.done:
            row.done = False
            # Метку чистим, а не храним «когда выполнили в первый раз»: она значит
            # «этот подход выполнен вот тогда». Если у упражнения не осталось
            # выполненных подходов, оно честно возвращается в план и получит новое
            # место, когда его действительно сделают.
            row.done_at = None
            row.save(update_fields=["done", "done_at"])
            row.workout.current_exercise = row.exercise
            row.workout.save(update_fields=["current_exercise"])
        return live_region_response(request, row.workout)


class SetDeleteView(LoginRequiredMixin, View):
    """«Убрать подход»: невыполненный в живом режиме, любой — в записанной.

    Выполненные подходы идущей тренировки неприкосновенны: их сначала
    возвращают в работу. Последний подход записанной не убирается — пустая
    силовая перестала бы быть силовой; убрать всё — это «Удалить тренировку».
    """

    def post(self, request, pk):
        row = editable_set_or_404(request, pk)
        workout = row.workout
        if workout.is_finished and not workout.sets.exclude(pk=row.pk).exists():
            return live_region_response(
                request,
                workout,
                open_set_id=row.pk,
                error="Это последний подход. Чтобы убрать всё, удалите тренировку.",
            )
        row.delete()
        # Номера не пересчитываются: на экране подходы нумеруются по позиции,
        # а перенумерация рисковала бы упереться в уникальный индекс.
        # Если это был последний подход упражнения, его заметке в тренировке
        # больше не место — иначе она всплыла бы при повторном добавлении.
        services.drop_orphan_notes(workout)
        services.drop_lone_circuits(workout)
        return live_region_response(request, workout)


class WorkoutLocationView(LoginRequiredMixin, View):
    """Место тренировки: модалка на GET, сохранение на POST.

    Тренировка берётся любая своя, в любом состоянии. Не live_workout_or_404:
    забытое место записанной тренировки иначе осталось бы неисправимым — а
    сравнение по залам строилось бы на вранье. Правится из итога и с экрана
    правки. Чужая по прямому URL даёт 404 фильтром по user.
    """

    def get_workout(self):
        return get_object_or_404(
            Workout.objects.filter(user=self.request.user).select_related("location"),
            pk=self.kwargs["pk"],
        )

    def get(self, request, pk):
        workout = self.get_workout()
        return render(
            request,
            "workouts/_location_modal.html",
            {
                "workout": workout,
                "locations": Location.objects.filter(owner=request.user),
                "location_max_length": LOCATION_NAME_MAX_LENGTH,
            },
        )

    def post(self, request, pk):
        workout = self.get_workout()
        # Своё поле перебивает выбранную строку — правило chosen_muscle_group.
        name = collapse_spaces(request.POST.get("location_own", ""))[:LOCATION_NAME_MAX_LENGTH]
        raw = request.POST.get("location", "")
        if name:
            workout.location = services.location_for_name(request.user, name)
        elif raw.isdecimal():
            # Священное правило: чужое место по прямому id — 404.
            workout.location = get_object_or_404(
                Location.objects.filter(owner=request.user), pk=int(raw)
            )
        else:
            # Строка «Без места» и пустая отправка означают «убрать место».
            workout.location = None
        workout.save(update_fields=["location"])
        # Только OOB-значение: пустой остаток ответа закрывает модалку.
        return render(request, "workouts/_location_value.html", {"workout": workout, "oob": True})


class WorkoutCardioPartView(LoginRequiredMixin, View):
    """Кардио-часть тренировки: модалка на GET, сохранение и удаление на POST.

    Схема та же, что у WorkoutLocationView, и тренировка так же берётся любая
    своя в любом состоянии: дописать заминку к уже записанной тренировке нужно
    по тому же доводу, по которому там же правится место — экрана правки
    силовой в проекте нет.

    Ответ — HX-Redirect, а не OOB-фрагмент: на экране меняются два разных куска,
    список частей и плитка итогов. Тот же выбор, что у WorkoutTimeView.
    """

    def get_workout(self):
        return get_object_or_404(
            Workout.objects.filter(user=self.request.user).select_related("sport"),
            pk=self.kwargs["pk"],
        )

    def get_part(self, workout):
        """Правимая часть либо None — тогда это создание новой.

        Часть ищется среди частей ЭТОЙ тренировки: чужая по прямому id даёт 404
        и так, но лишний фильтр делает невозможной и подстановку своей части от
        другой тренировки.
        """
        part_pk = self.kwargs.get("part_pk")
        if part_pk is None:
            return None
        return get_object_or_404(workout.cardio_parts, pk=part_pk)

    def render_modal(self, request, workout, form, part):
        return render(
            request,
            "workouts/_cardio_part_modal.html",
            {
                "workout": workout,
                "form": form,
                "part": part,
                "sports": form.fields["sport"].queryset,
                "selected_sport_id": form["sport"].value(),
            },
        )

    def get(self, request, pk, part_pk=None):
        workout = self.get_workout()
        part = self.get_part(workout)
        form = CardioPartForm(user=request.user, workout=workout, instance=part)
        return self.render_modal(request, workout, form, part)

    def post(self, request, pk, part_pk=None):
        workout = self.get_workout()
        part = self.get_part(workout)
        if part is not None and request.POST.get("delete"):
            part.delete()
            return self.done(request, workout, "Кардио убрано.")

        form = CardioPartForm(request.POST, user=request.user, workout=workout, instance=part)
        if not form.is_valid():
            return self.render_modal(request, workout, form, part)
        form.save()
        return self.done(request, workout, "Кардио сохранено." if part else "Кардио добавлено.")

    @staticmethod
    def done(request, workout, message):
        messages.success(request, message)
        # Куда вернуться, решает содержимое тренировки — то же правило, что и у
        # маршрутизации экранов: с подходами дом это живой режим или итог, без
        # них — форма кардио.
        if not workout.sets.exists():
            url = reverse("workout_edit", args=[workout.pk])
        elif workout.is_finished:
            url = reverse("workout_summary", args=[workout.pk])
        else:
            url = reverse("workout_live", args=[workout.pk])
        return HttpResponse(headers={"HX-Redirect": url})


class WorkoutPlannedForView(LoginRequiredMixin, View):
    """День, на который подготовлен черновик: модалка на GET, сохранение на POST.

    Схема ровно та же, что у WorkoutLocationView: ответ на POST — пустое тело
    плюс OOB-обновление значения, поэтому модалка закрывается сама.

    Нужна силовому черновику: у кардио день ставится в форме плана, а силовой
    создаётся одним тапом из чузера, и другого места спросить попросту нет.
    Тренировка берётся любая своя, но не начатая: у идущей и записанной
    планового дня не бывает — это держит констрейнт planned_for_only_when_planned.
    """

    def get_workout(self):
        return get_object_or_404(
            Workout.objects.filter(user=self.request.user).planned(),
            pk=self.kwargs["pk"],
        )

    def get(self, request, pk):
        return render(
            request,
            "workouts/_planned_for_modal.html",
            {"workout": self.get_workout(), "today": timezone.localdate()},
        )

    def post(self, request, pk):
        workout = self.get_workout()
        raw = "" if request.POST.get("clear") else request.POST.get("planned_for", "")
        # Пустая строка — законный ввод: «убрать день». Мусор тоже уводит в
        # пустоту, а не в 500: это тот же выбор, что у фильтров истории.
        workout.planned_for = parse_day(raw)
        workout.save(update_fields=["planned_for"])
        return render(
            request, "workouts/_planned_for_value.html", {"workout": workout, "oob": True}
        )


def time_modal_response(request, workout, form, *, action, title, submit_label, error=None):
    """Модалка «когда и сколько» — одна на запись черновика и на правку.

    Заголовок, адрес и подпись кнопки приходят параметрами: поля и проверки у
    двух сценариев совпадают ровно, и вторая копия шаблона разошлась бы с первой.
    """
    return render(
        request,
        "workouts/_workout_time_modal.html",
        {
            "workout": workout,
            "form": form,
            "action": action,
            "title": title,
            "submit_label": submit_label,
            "error": error,
        },
    )


class WorkoutBackdateView(LoginRequiredMixin, View):
    """Запись подготовленного черновика за прошедший день.

    Так в приложение переезжает бумажная тетрадка: состав набирается обычными
    экранами живого режима, а здесь спрашивается ровно то, чего у черновика нет
    по определению, — когда это было и сколько длилось.
    """

    def get_workout(self):
        # Только свой силовой черновик. У кардио тренировка вводится формой
        # целиком, у идущей есть «Завершить», у записанной — правка времени.
        return get_object_or_404(
            Workout.objects.filter(STRENGTH_WORKOUT, user=self.request.user).planned().distinct(),
            pk=self.kwargs["pk"],
        )

    @staticmethod
    def content_error(workout):
        """Почему черновик нельзя записать как есть, либо None.

        Подход с пустым значением не выбрасываем молча: при переносе тетрадки
        потерянная строка незаметна, а порог тот же, что у «Подход выполнен».
        """
        rows = list(workout.sets.select_related("exercise").order_by("id"))
        if not rows:
            return "В черновике нет упражнений."
        for row in rows:
            field, message = REQUIRED_FIELD[row.measurement]
            if getattr(row, field) < 1:
                return f"{row.exercise.name}: {message}"
        return None

    def render_modal(self, request, workout, form, error=None):
        return time_modal_response(
            request,
            workout,
            form,
            action=reverse("workout_backdate", args=[workout.pk]),
            title="Когда была тренировка",
            submit_label="Записать",
            error=error,
        )

    def get(self, request, pk):
        workout = self.get_workout()
        # Кнопка стоит наверху любого черновика, и пустого тоже: что записать
        # нельзя, окно говорит сразу, а не после заполнения даты и длительности.
        return self.render_modal(request, workout, StrengthTimeForm(), self.content_error(workout))

    def post(self, request, pk):
        workout = self.get_workout()
        form = StrengthTimeForm(request.POST)
        error = self.content_error(workout) if form.is_valid() else None
        if not form.is_valid() or error:
            return self.render_modal(request, workout, form, error)

        with transaction.atomic():
            # Метка времени подхода остаётся пустой: в тетрадке её нет, а NULL
            # у выполненного подхода как раз и значит «времени не знаем».
            # Порядок упражнений от этого не страдает — exercise_order_key без
            # меток раскладывает тренировку по порядку добавления.
            workout.sets.filter(done=False).update(done=True)
            # Одним UPDATE: между двумя строка на мгновение выглядела бы идущей
            # и упёрлась бы в unique_live_workout_per_user, если тренировка у
            # пользователя уже идёт (частичный индекс проверяется немедленно).
            # Тем же UPDATE гасится planned_for — его держит констрейнт.
            # Условие по started_at делает безвредным даблтап: 0 строк.
            Workout.objects.filter(pk=workout.pk, started_at__isnull=True).update(
                started_at=form.cleaned_data["started_at"],
                duration_min=form.cleaned_data["duration_min"],
                planned_for=None,
            )
        messages.success(request, "Тренировка записана.")
        # HX-Redirect, а не OOB: черновик становится итогом, меняется весь экран.
        return HttpResponse(headers={"HX-Redirect": reverse("workout_summary", args=[workout.pk])})


class WorkoutTimeView(LoginRequiredMixin, View):
    """Правка даты, времени и длительности записанной силовой тренировки.

    Экрана правки силовой в проекте нет — по той же причине, по которой место
    правится точечным эндпоинтом: промах днём при переносе тетрадки иначе
    остался бы неисправимым.
    """

    def get_workout(self):
        workout = get_object_or_404(
            Workout.objects.filter(user=self.request.user).finished().select_related("sport"),
            pk=self.kwargs["pk"],
        )
        # Дискриминатор — подходы, а не категория: у чистого кардио дата
        # правится своей формой, а у смешанной — здесь, вместе с силовой.
        if not owns_sets(workout):
            raise Http404("У кардио свой экран правки")
        return workout

    def render_modal(self, request, workout, form):
        return time_modal_response(
            request,
            workout,
            form,
            action=reverse("workout_time", args=[workout.pk]),
            title="Когда была тренировка",
            submit_label="Сохранить",
        )

    def get(self, request, pk):
        workout = self.get_workout()
        return self.render_modal(request, workout, StrengthTimeForm(instance=workout))

    def post(self, request, pk):
        workout = self.get_workout()
        form = StrengthTimeForm(request.POST)
        if not form.is_valid():
            return self.render_modal(request, workout, form)

        started_at = form.cleaned_data["started_at"]
        delta = started_at - workout.started_at
        with transaction.atomic():
            if delta:
                # У тренировки, записанной живым режимом, метки настоящие: без
                # сдвига они остались бы в прежнем дне. Пустые остаются пустыми,
                # порядок упражнений сдвиг не меняет.
                workout.sets.filter(done_at__isnull=False).update(done_at=F("done_at") + delta)
            workout.started_at = started_at
            workout.duration_min = form.cleaned_data["duration_min"]
            # Одного save хватает: duration_min остаётся непустым, и в частичный
            # индекс идущих тренировок строка не попадает ни на мгновение.
            workout.save(update_fields=["started_at", "duration_min"])
        messages.success(request, "Тренировка обновлена.")
        # Меняются два разных куска итога — дата в шапке и «время» в карточке.
        return HttpResponse(headers={"HX-Redirect": reverse("workout_summary", args=[workout.pk])})


class WorkoutFinishView(LoginRequiredMixin, View):
    """Завершение: длительность от started_at, плановые подходы удаляются."""

    def get_workout(self):
        workout = get_object_or_404(
            Workout.objects.filter(user=self.request.user).select_related("sport"),
            pk=self.kwargs["pk"],
        )
        # Шлагбаума по категории здесь больше нет: завершать можно любую идущую
        # тренировку — и смешанную, и ту, где силовой оказалась только заминка.
        if workout.is_planned:
            # Нечего завершать: время не шло. Заодно защищает elapsed_min от NULL.
            raise Http404("Тренировка ещё не начата")
        return workout

    def get(self, request, pk):
        workout = self.get_workout()
        if workout.is_finished:
            # Модалка запрошена из устаревшей вкладки. За обычным 302 htmx пошёл бы
            # сам и вставил страницу итога внутрь #modal — HX-Redirect вместо этого
            # выполняет полноценный переход браузера.
            if request.headers.get("HX-Request"):
                summary_url = reverse("workout_summary", args=[workout.pk])
                return HttpResponse(headers={"HX-Redirect": summary_url})
            return redirect("workout_summary", pk=workout.pk)
        form = None
        if asks_duration(workout):
            form = FinishDurationForm(
                elapsed_min=workout.elapsed_min, initial=finish_estimate(workout)
            )
        return self.render_modal(request, workout, form)

    def render_modal(self, request, workout, form):
        return render(
            request,
            "workouts/_finish_modal.html",
            {
                "workout": workout,
                "done_count": workout.sets.filter(done=True).count(),
                "form": form,
            },
        )

    def post(self, request, pk):
        workout = self.get_workout()
        if workout.is_finished:
            # Даблтап или кнопка «назад»: тренировка уже завершена.
            return redirect("workout_summary", pk=workout.pk)
        duration = max(1, min(MAX_DURATION_HOURS * 60, workout.elapsed_min))
        if asks_duration(workout):
            # Забытая: время с начала — это сутки, а не тренировка. Окно с
            # ошибкой возвращается в #modal (форма шлёт hx-post), успех — тем же
            # редиректом, что и без неё, только через HX-Redirect.
            form = FinishDurationForm(request.POST, elapsed_min=workout.elapsed_min)
            if not form.is_valid():
                return self.render_modal(request, workout, form)
            duration = form.cleaned_data["duration_min"]
        workout.sets.filter(done=False).delete()
        # Упражнение, которое так и не сделали, уходит вместе с плановыми
        # подходами — и его заметка тоже.
        services.drop_orphan_notes(workout)
        services.drop_lone_circuits(workout)
        # Кардио-части считаются содержимым наравне с подходами: иначе
        # завершение тренировки, где успели только пробежку, стёрло бы её.
        if not workout.sets.exists() and not workout.cardio_parts.exists():
            workout.delete()
            messages.info(request, "Тренировка не записана: нет ни подходов, ни кардио.")
            return finish_redirect(request, reverse("workout_history"))
        workout.duration_min = duration
        workout.save(update_fields=["duration_min"])
        messages.success(request, "Тренировка записана.")
        if not workout.sets.exists():
            # Подходов нет — дом такой тренировки форма кардио, а не итог.
            return finish_redirect(request, reverse("workout_edit", args=[workout.pk]))
        return finish_redirect(request, reverse("workout_summary", args=[workout.pk]))


def asks_duration(workout):
    """Спрашивать ли длительность при завершении — только у забытой, которую запишут.

    Без выполненных подходов и кардио тренировка при завершении стирается, и
    обязательное поле времени только мешало бы её выбросить.
    """
    return workout.is_stale and (
        workout.sets.filter(done=True).exists() or workout.cardio_parts.exists()
    )


def finish_redirect(request, url):
    """Переход после завершения: из окна забытой (htmx) — HX-Redirect, иначе 302."""
    if request.headers.get("HX-Request"):
        return HttpResponse(headers={"HX-Redirect": url})
    return redirect(url)


def finish_estimate(workout):
    """Подсказка длительности забытой тренировки — до последнего выполненного подхода.

    Меток нет (ничего не выполнили или подходы записаны без них) — поле пустое:
    угадывать длительность не из чего.
    """
    last = workout.sets.filter(done_at__isnull=False).aggregate(last=Max("done_at"))["last"]
    if last is None:
        return {}
    minutes = max(1, math.ceil((last - workout.started_at).total_seconds() / 60))
    hours, minutes = divmod(minutes, 60)
    return {"duration_hours": hours or None, "duration_minutes": minutes or None}


class WorkoutSummaryView(LoginRequiredMixin, View):
    """Итог силовой тренировки: упражнения с подходами и метрика нагрузки."""

    def get(self, request, pk):
        workout = get_object_or_404(
            # Аннотации нагрузки — тем же запросом: их читает workout.workload,
            # а метрика итога должна совпадать с карточкой в ленте.
            Workout.objects.filter(user=request.user)
            .annotate(**stats.WORKLOAD_ANNOTATIONS)
            .select_related("sport", "location")
            .prefetch_related(cardio_parts_prefetch()),
            pk=pk,
        )
        if not workout.is_finished:
            # Черновик и идущая живут на живом экране — он же решит, силовая
            # это или нет. Проверка идёт первой: у черновика подходов может не
            # быть вовсе, и по содержимому он уехал бы не туда.
            return redirect("workout_live", pk=workout.pk)
        groups = services.exercise_groups(workout)
        if not groups:
            # Подходов нет — дом такой тренировки форма кардио. Смотрим на уже
            # загруженные группы, а не спрашиваем sets.exists() отдельно: экран
            # всё равно их читает, и лишний запрос был бы данью формулировке.
            #
            # Не 404, а редирект: адрес итога становится безопасным для любой
            # записанной тренировки, и старые ссылки на кардио не ломаются.
            return redirect("workout_edit", pk=workout.pk)
        # Заголовок итога — та же подпись, что в ленте. Отдельный запрос на одну
        # тренировку: правило подписи дороже держать в одном месте, чем выводить
        # её тут заново из уже загруженных групп.
        stats.attach_muscle_groups(request.user, [workout])
        for group in groups:
            group["total"] = services.exercise_total(group["sets"])
        return render(
            request,
            "workouts/workout_summary.html",
            {
                "workout": workout,
                "groups": groups,
                "blocks": services.blocks(groups),
                "nav_active": "history",
            },
        )


class WorkoutCorrectView(LoginRequiredMixin, View):
    """Правка записанной силовой: подходы, упражнения, заметки.

    Это итог, в котором тап по подходу раскрывает те же степперы, что в живом
    режиме, а «+ подход» и «+ Упражнение» — те же эндпоинты: второго способа
    набирать подходы так и не появляется. Новые подходы записанной тренировки
    сразу выполненные, без метки времени, поэтому тоннаж, рекорды и порядок
    упражнений пересчитываются сами, а новое упражнение встаёт в конец.
    HTMX-запрос (?set=<id>) отдаёт регион с раскрытым подходом.
    """

    def get(self, request, pk):
        workout = get_object_or_404(
            Workout.objects.filter(user=request.user).select_related("sport", "user", "location"),
            pk=pk,
        )
        if not workout.is_finished:
            # Черновик и идущая правятся в живом режиме.
            return redirect("workout_live", pk=workout.pk)
        raw = request.GET.get("set", "")
        context = correct_context(workout, open_set_id=int(raw) if raw.isdecimal() else None)
        if not context["groups"]:
            # Подходов нет — дом такой тренировки форма кардио, как у итога.
            return redirect("workout_edit", pk=workout.pk)
        if request.headers.get("HX-Request"):
            return render(request, "workouts/_correct_exercises.html", context)
        stats.attach_muscle_groups(request.user, [workout])
        return render(request, "workouts/workout_correct.html", context | {"nav_active": "history"})


class WorkoutRepeatView(LoginRequiredMixin, View):
    """«Повторить»: новая активная тренировка с тем же набором упражнений.

    Веса подставляются не из источника, а из последней тренировки с каждым
    упражнением — по общему правилу подстановки.
    """

    def post(self, request, pk):
        source = get_object_or_404(
            # Повторяют то, что можно повторить набором упражнений, — то есть
            # тренировку с подходами. Кардио-части при этом не копируются: они
            # результат, а не план, ровно как веса и место.
            Workout.objects.filter(user=request.user, sets__isnull=False)
            .finished()
            .select_related("sport")
            .distinct(),
            pk=pk,
        )
        active = Workout.objects.filter(user=request.user).live().first()
        if active is not None:
            messages.info(request, "Сначала завершите текущую тренировку.")
            return redirect("workout_live", pk=active.pk)
        # Место берём текущее, а не из источника: «повторить» значит «сделать то
        # же самое сегодня», а не «скопировать запись». Повтор тренировки из
        # командировочного зала иначе приписал бы сегодняшнюю сессию тому залу —
        # и сравнение по залам поехало бы. Веса и отдых тоже не копируются.
        location = Location.objects.default_for(request.user)
        try:
            with transaction.atomic():
                workout = Workout.objects.create(
                    user=request.user,
                    sport=source.sport,
                    location=location,
                    started_at=timezone.now(),
                    duration_min=None,
                )
        except IntegrityError:
            active = Workout.objects.filter(user=request.user).live().first()
            if active is None:
                raise
            return redirect("workout_live", pk=active.pk)
        for group in services.exercise_groups(source):
            # Круги переносятся: «повторить» — сделать то же, а суперсет — часть «того же».
            services.create_planned_sets(workout, group["exercise"], circuit=group["circuit_no"])
        return redirect("workout_live", pk=workout.pk)


class SportCreateView(LoginRequiredMixin, View):
    """HTMX-модалка: личный вид спорта создаётся не уходя с формы тренировки."""

    def get(self, request):
        return self.render_modal(SportForm(user=request.user))

    def post(self, request):
        form = SportForm(request.POST, user=request.user)
        if not form.is_valid():
            return self.render_modal(form)

        sport = form.save()
        # Модалку закрываем (пустой контейнер), а блок чипов обновляем out-of-band,
        # чтобы новый вид спорта сразу оказался выбранным.
        chips = render_to_string(
            "workouts/_sport_chips.html",
            {
                "sports": Sport.objects.visible_to(request.user).filter(
                    category=Sport.Category.CARDIO
                ),
                "selected_id": sport.pk,
                "oob": True,
            },
            request=request,
        )
        return HttpResponse(chips)

    def render_modal(self, form):
        return render(self.request, "workouts/_sport_modal.html", {"form": form})


# ---------- Дашборд ----------


class DashboardView(LoginRequiredMixin, TemplateView):
    """Главная: сводка за период, часы по неделям, рекорды, последние тренировки.

    Период меняет только сводку (заголовок и плитки): график, рекорды и
    последние тренировки от него не зависят. Выбор живёт в адресе — обновление
    и «назад» его сохраняют, а новое открытие начинается с 7 дней.
    """

    template_name = "workouts/dashboard.html"
    extra_context = {"nav_active": "dashboard"}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.localdate()
        period = parse_period(self.request.GET, today)
        chart = stats.weekly_chart(user)
        # Цель недели — из минут того же графика: ни одного запроса сверху.
        goal = stats.week_goal(user.weekly_goal_minutes, chart["totals"], timezone.localdate())
        # Рекорды считаются один раз: прожектору нужен тот же топ, что и плиткам.
        strength = stats.strength_records(user, limit=STRENGTH_RECORDS_LIMIT)
        # Подготовленное — первым делом: смысл плана на неделю в том, чтобы он
        # был перед глазами, а не находился в модалке. Идущая тренировка сюда не
        # идёт: её показывает кнопка «Продолжить» в том же чузере.
        _, drafts = unfinished_workouts(user)
        context.update(
            {
                "planned": drafts,
                "period": period,
                "period_chips": period_chips(period),
                "summary": stats.period_summary(user, period, today),
                "goal": goal,
                "chart": chart,
                "has_chart": bool(chart["datasets"]),
                "latest": stats.latest_workouts(user),
                "records": self.build_records(user, strength),
                "spotlight": stats.exercise_spotlight(user, records=strength),
            }
        )
        return context

    @staticmethod
    def build_records(user, strength):
        """Единый список плиток рекордов: топ силовых + кардио по видам.

        Первый силовой на десктопе уходит в карточку-прожектор (is_spotlight).
        """
        records = [
            {
                "label": row["name"],
                "value": row["value_display"],
                # Подпись метрики — только у непривычных единиц: у веса она
                # очевидна из «кг», а «удержание» и «повторы» стоит назвать.
                "sub": ""
                if row["measurement"] == Exercise.Measurement.WEIGHT_REPS
                else row["metric_label"],
                "url": reverse("exercise_detail", args=[row["exercise_id"]]),
                "is_spotlight": index == 0,
            }
            for index, row in enumerate(strength)
        ]
        records += [
            {
                "label": f"{row['name']} · дистанция",
                "value": f"{row['distance_display']} км",
                "sub": row["metric_display"],
                "url": "",
                "is_spotlight": False,
            }
            for row in stats.cardio_records(user)
        ]
        return records


class DashboardStatsView(LoginRequiredMixin, View):
    """Карточка «Статистика» дашборда на ПК: время, тоннаж и дистанция за год.

    Дашборд рендерит только заготовку карточки, а данные она забирает сама,
    когда попадает в поле зрения (hx-trigger="intersect once"): на телефоне
    карточка скрыта, IntersectionObserver её не видит, и запроса нет вовсе —
    а у самого дашборда бюджет запросов не двигается.
    """

    def get(self, request):
        data = stats.monthly_stats(request.user)
        return render(
            request,
            "workouts/_dashboard_stats.html",
            {"stats": data, "has_data": any(tab["datasets"] for tab in data["tabs"])},
        )


class DashboardPeriodView(LoginRequiredMixin, View):
    """Окно «Свой период»: две даты и GET-форма на главную.

    Окно приходит htmx-ом в #modal, как остальные. Отправка — обычный переход
    по адресу с from и to, поэтому выбор живёт в адресе, а «назад» возвращает
    прежний период. Даты подставлены из открытой сводки.
    """

    def get(self, request):
        today = timezone.localdate()
        first = parse_day(request.GET.get("from")) or today - timedelta(days=6)
        last = parse_day(request.GET.get("to")) or today
        return render(
            request,
            "workouts/_period_modal.html",
            {"first": first, "last": last, "today": today, "earliest": stats.EARLIEST_DAY},
        )


class DashboardWeekView(LoginRequiredMixin, View):
    """Партиал по тапу на столбец графика: тренировки выбранной недели."""

    def get(self, request):
        try:
            start = week_start(date.fromisoformat(request.GET.get("start", "")))
            # OverflowError: дата у самого края календаря (год 9999) переполняет
            # арифметику недели — это такой же негодный ввод, как и мусор.
            first_moment, next_week = stats.day_bounds(start, start + timedelta(days=6))
        except (TypeError, ValueError, OverflowError):
            return HttpResponseBadRequest("Недопустимая дата")
        workouts = (
            Workout.objects.filter(user=request.user)
            .finished()
            .filter(started_at__gte=first_moment, started_at__lt=next_week)
            .select_related("sport")
            .prefetch_related(cardio_parts_prefetch())
            .annotate(**stats.WORKLOAD_ANNOTATIONS)
            .order_by("-started_at", "-id")
        )
        today = timezone.localdate()
        return render(
            request,
            "workouts/_dashboard_week.html",
            {
                "title": week_title(start, today),
                "rows": [
                    stats.workout_row(workout, today)
                    for workout in stats.attach_muscle_groups(request.user, workouts)
                ],
            },
        )


def machines_by_location(user, exercise):
    """Места пользователя с тренажёром этого упражнения в каждом — одним запросом.

    У каждого места атрибут machine («Technogym · Selection 900» или пусто).
    Подзапросы к строке места, а не джойн: строки тренажёра может не быть.
    """
    machines = ExerciseMachine.objects.filter(user=user, exercise=exercise, location=OuterRef("pk"))
    places = list(
        Location.objects.filter(owner=user).annotate(
            machine_brand=Subquery(machines.values("brand__name")[:1]),
            machine_model=Subquery(machines.values("model__name")[:1]),
        )
    )
    for place in places:
        place.machine = machine_label(place.machine_brand or "", place.machine_model or "")
    return places


def exercise_detail_context(request, exercise, *, in_panel):
    """Контекст страницы упражнения.

    Отдельной функцией, а не методом вьюхи: тот же контекст собирает
    переименование — оно отвечает перерисованным телом страницы, и второй,
    сокращённой копии этой сборки быть не должно.
    """
    progress = stats.exercise_progress(request.user, exercise)
    count = len(progress)
    metric_label = METRIC_LABELS[exercise.measurement]
    if count:
        record = max(group["max_value"] for group in progress)
        workouts_word = ru_plural(count, "тренировка", "тренировки", "тренировок")
        stats_line = f"{count} {workouts_word}"
        if record:
            stats_line += f" · рекорд {metric_display(exercise.measurement, record)}"
    else:
        stats_line = "ещё не было в тренировках"
    facets = facets_for(request.user)
    # Десятый запрос страницы: места с тренажёрами. Он же подписывает тренажёр
    # у записей истории — по месту той тренировки, без запроса на запись.
    places = machines_by_location(request.user, exercise)
    machine_at = {place.pk: place.machine for place in places}
    for group in progress:
        group["machine"] = machine_at.get(group["workout"].location_id, "")
    return {
        "exercise": exercise,
        "history": list(reversed(progress)),
        "machine_places": places,
        "machines_open": suggests_machine(exercise) or any(place.machine for place in places),
        "is_machine": is_machine(exercise),
        "chart": {
            "labels": [group["label"] for group in progress],
            "values": [group["max_value"] for group in progress],
            "colorKey": "strength",
            # Время подписывается как 1:30, поэтому формат отдельно от единицы.
            "unit": METRIC_UNITS[exercise.measurement],
            "format": "time" if exercise.measurement in TIME_MEASUREMENTS else "",
        },
        "chart_title": f"Максимум: {metric_label}",
        "metric_label": metric_label,
        "can_edit": can_edit_exercise(request.user, exercise),
        **weight_step_context(exercise),
        # Обе оси приезжают одним запросом, поэтому второй блок чипов бюджету
        # страницы (девять запросов) ничего не стоил.
        "muscle_groups": facets.muscle_groups,
        "max_length": MUSCLE_GROUP_MAX_LENGTH,
        **equipment_context(exercise, facets),
        "stats_line": stats_line,
        # Разрез по местам считается в Python по уже загруженным
        # группам — ни одного нового запроса, бюджет страницы цел.
        "by_location": stats.progress_by_location(exercise, progress),
        "in_panel": in_panel,
        "nav_active": "exercises",
    }


def equipment_context(exercise, facets, *, saved=False):
    """Контекст блока «Снаряд»: он же приходит и на странице, и после правки."""
    return {
        "equipment_list": facets.equipment,
        "equipment_max_length": EQUIPMENT_MAX_LENGTH,
        "equipment_saved": saved,
    }


class ExerciseDetailView(LoginRequiredMixin, View):
    """Страница упражнения: график метрики и история подходов.

    Страница глобального упражнения видна всем, но данные — только свои:
    прогресс фильтруется по request.user.
    """

    def get(self, request, pk):
        exercise = visible_exercise_with_step(request.user, pk)
        # Панель мастер-детали и отдельная страница — один и тот же контент:
        # HTMX-запрос получает только тело, обычный — тело внутри базы. Тот же
        # приём, что у WorkoutHistoryView. Отдельного URL нет намеренно: иначе
        # у упражнения появилась бы вторая дверь, которую пришлось бы защищать
        # отдельно. Если однажды появится hx-push-url, условие обязано стать
        # «HX-Request и не HX-History-Restore-Request»: при промахе своего кеша
        # истории htmx дозапрашивает URL с обоими заголовками и ждёт страницу.
        in_panel = bool(request.headers.get("HX-Request"))
        template = (
            "workouts/_exercise_detail_body.html" if in_panel else "workouts/exercise_detail.html"
        )
        return render(
            request, template, exercise_detail_context(request, exercise, in_panel=in_panel)
        )


def editable_exercises(user):
    """Упражнения, которые человек может править: свои, а администратор — ещё и общие.

    Одна точка для всех правок упражнения (единица, группа, снаряд, название,
    удаление). Чужое личное не правит никто, и администратор тоже: это данные
    другого человека, а не справочник проекта.
    """
    if user.is_admin:
        return Exercise.objects.filter(Q(owner=user) | Q(owner__isnull=True))
    return Exercise.objects.filter(owner=user)


def can_edit_exercise(user, exercise):
    """То же правило для уже загруженного упражнения — без запроса."""
    return exercise.owner_id == user.pk or (exercise.is_global and user.is_admin)


class ExerciseMeasurementView(LoginRequiredMixin, View):
    """Смена единицы своего упражнения (у админа — и общего). Записанные подходы не
    меняются: у них свой снимок единицы, и история остаётся такой, как её записали."""

    def post(self, request, pk):
        # Общее упражнение правит только админ, чужое личное — никто.
        exercise = get_object_or_404(editable_exercises(request.user), pk=pk)
        measurement = request.POST.get("measurement", "")
        if measurement not in Exercise.Measurement.values:
            return HttpResponseBadRequest("Неизвестная единица")
        exercise.measurement = measurement
        exercise.save(update_fields=["measurement"])
        return render(
            request,
            "workouts/_measurement_choice.html",
            {"exercise": exercise, "can_edit": True, "saved": True},
        )


# ---------- Справочники в профиле и новости ----------


def usage_label(count):
    """Подпись строки справочника: «в 3 тренировках» или «не использовалось»."""
    if not count:
        return "не использовалось"
    word = ru_plural(count, "тренировке", "тренировках", "тренировках")
    return f"в {count} {word}"


NO_MUSCLE_GROUP_TITLE = "Без группы"


def group_by_muscle(exercises):
    """Упражнения по группам мышц: [{"title", "items"}], «Без группы» последней.

    Группировка в Python по уже загруженному списку: отдельных запросов это не
    стоит, а порядок внутри группы остаётся тем, что задал queryset.
    """
    buckets = {}
    for exercise in exercises:
        buckets.setdefault(exercise.muscle_group or "", []).append(exercise)
    titles = sorted(title for title in buckets if title)
    if "" in buckets:
        titles.append("")
    return [{"title": title or NO_MUSCLE_GROUP_TITLE, "items": buckets[title]} for title in titles]


def trained_first(exercises):
    """Упражнения, которые пользователь действительно делал, — свежие сверху.

    Признак — записанная тренировка (`workouts_count`), а не наличие рекорда:
    метрика весовых упражнений — вес, поэтому у подтягиваний с нулевым весом и у
    упражнения со сменённой единицей рекорда нет, а тренировок двадцать.

    Порядок — «что делал последним»: блок отвечает на вопрос «открыть то, что я
    делаю», а рейтинг по числу тренировок замерзает и перестаёт следить за
    текущей программой.
    """
    trained = [exercise for exercise in exercises if exercise.workouts_count]
    # Два прохода стабильной сортировкой: у ключей разные направления, и дату не
    # приходится выворачивать в отрицательное число. Тайбрейк обязателен —
    # last_workout_at это время начала ТРЕНИРОВКИ, поэтому у всех упражнений одной
    # сессии он совпадает побитово, и без второго ключа порядок был бы случайным.
    trained.sort(key=lambda exercise: (-exercise.workouts_count, exercise.name))
    trained.sort(key=lambda exercise: exercise.last_workout_at, reverse=True)
    return trained


class CatalogSort(NamedTuple):
    """Колонка таблицы справочника на ПК, по которой можно сортировать."""

    label: str
    # Для подписи таблицы читалкам: «Упражнения по названию, по возрастанию».
    caption: str
    # None — порядок запроса, то есть по названию: сравнивает база, её правилами.
    key: Callable | None
    # У строки нет значения (нет группы, не тренировал) — в хвост при любом
    # направлении: десяток прочерков сверху скрыл бы то, ради чего сортировали.
    missing: Callable | None
    # Направление первого клика: числа и даты — сначала большие и свежие.
    descending: bool


CATALOG_SORTS = {
    "name": CatalogSort("Упражнение", "по названию", None, None, False),
    "group": CatalogSort(
        "Группа", "по группе мышц", attrgetter("muscle_group"), lambda e: not e.muscle_group, False
    ),
    "workouts": CatalogSort(
        "Тренировок",
        "по числу тренировок",
        lambda e: (e.workouts_count, e.last_workout_at),
        lambda e: not e.workouts_count,
        True,
    ),
    "last": CatalogSort(
        "Последняя",
        "по дате последней тренировки",
        lambda e: (e.last_workout_at, e.workouts_count),
        lambda e: not e.workouts_count,
        True,
    ),
}
# Свежее сверху — тот же порядок, что у плиток «Я тренирую» на телефоне: таблица
# на ПК их заменяет, и первым на экране должно быть то, что человек делает сейчас.
DEFAULT_CATALOG_SORT = ("last", True)


def parse_catalog_sort(raw):
    """«-last» → ("last", True). Мусор молча даёт умолчание — как неизвестный чип."""
    raw = (raw or "").strip()
    field = raw.removeprefix("-")
    if field not in CATALOG_SORTS:
        return DEFAULT_CATALOG_SORT
    return field, raw.startswith("-")


def catalog_sort_param(field, desc):
    """Значение sort для ссылки; у умолчания параметра нет — адрес остаётся чистым."""
    if (field, desc) == DEFAULT_CATALOG_SORT:
        return None
    return f"-{field}" if desc else field


def sort_catalog(exercises, field, desc):
    """Строки таблицы справочника в выбранном порядке.

    Сортируем в Python уже загруженный список, а не в запросе: телефонные
    раскладки (группы, плитки) читают тот же список по названию, и порядок
    таблицы не должен их задевать — а нового запроса это не стоит. Сортировки
    стабильные, поэтому равные значения остаются в порядке названий из базы.
    """
    rows = list(exercises)
    sort = CATALOG_SORTS[field]
    if sort.key is None:
        return rows[::-1] if desc else rows
    present = [row for row in rows if not sort.missing(row)]
    absent = [row for row in rows if sort.missing(row)]
    present.sort(key=sort.key, reverse=desc)
    return present + absent


def catalog_columns(field, desc):
    """Заголовки таблицы: подпись, aria-sort у активной и ссылка следующего клика.

    Клик по активной колонке разворачивает направление, по другой — включает её
    с направлением первого клика. Рекорд не сортируется: в нём смешаны
    килограммы, повторы и время.
    """
    columns = []
    for key, sort in CATALOG_SORTS.items():
        active = key == field
        columns.append(
            {
                "field": key,
                "label": sort.label,
                "sortable": True,
                "aria_sort": ("descending" if desc else "ascending") if active else "",
                "param": catalog_sort_param(key, not desc if active else sort.descending),
            }
        )
    columns.append({"field": "record", "label": "Рекорд", "sortable": False, "aria_sort": ""})
    return columns


def last_workout_label(moment, today):
    """Дата последней тренировки для таблицы: «25 авг», с годом — если не текущий."""
    if not moment:
        return ""
    local = timezone.localtime(moment)
    return formats.date_format(local, "j b" if local.year == today.year else "j b Y")


def visible_exercise_with_step(user, pk):
    """Видимое упражнение вместе с шагом веса этого пользователя — одним запросом."""
    # Автор общей записи — JOIN'ом: администратору страница показывает его почту.
    # Последняя своя заявка в общий справочник (строка статуса) — подзапросами
    # в том же SELECT: бюджет страницы упражнения не двигается.
    latest = CatalogRequest.objects.filter(exercise=OuterRef("pk"), user=user).order_by(
        "-created_at", "-pk"
    )
    queryset = with_weight_step(
        Exercise.objects.visible_to(user)
        .select_related("contributed_by")
        .annotate(
            request_pk=Subquery(latest.values("pk")[:1]),
            request_status=Subquery(latest.values("status")[:1]),
            request_reason=Subquery(latest.values("reason")[:1]),
        ),
        user.pk,
        exercise_ref="pk",
    )
    return get_object_or_404(queryset, pk=pk)


def weight_step_context(exercise, *, saved=False, error=""):
    """Контекст блока «Шаг веса» — и на странице упражнения, и в ответе на сохранение.

    Шаг берётся из аннотации visible_exercise_with_step: отдельный запрос сделал бы
    страницу упражнения дороже ради одного числа.
    """
    step = getattr(exercise, "weight_step", None) or DEFAULT_WEIGHT_STEP
    return {
        "exercise": exercise,
        "weight_step": step,
        "weight_step_display": decimal_display(step),
        "weight_step_choices": [
            {"value": value, "display": decimal_display(value), "chosen": value == step}
            for value in WEIGHT_STEP_CHOICES
        ],
        # Своё значение показываем в поле, только если оно не совпало ни с одним чипом.
        "weight_step_own": ""
        if any(value == step for value in WEIGHT_STEP_CHOICES)
        else decimal_display(step),
        "shows_weight_step": "weight_kg" in MEASUREMENT_FIELDS[exercise.measurement],
        "saved": saved,
        "error": error,
    }


class ExerciseWeightStepView(LoginRequiredMixin, View):
    """Шаг кнопок «−» и «+» для веса этого упражнения.

    В отличие от единицы и группы мышц правится у ЛЮБОГО видимого упражнения,
    включая глобальные: это личная настройка, чужих данных она не трогает, а
    настроить шаг у «Приседаний со штангой» — ровно тот случай, ради которого
    настройка и появилась.
    """

    def post(self, request, pk):
        exercise = visible_exercise_with_step(request.user, pk)
        if "weight_kg" not in MEASUREMENT_FIELDS[exercise.measurement]:
            return HttpResponseBadRequest("У этого упражнения нет веса")

        # Своё поле перебивает чип — то же правило, что у группы мышц.
        raw = request.POST.get("weight_step_own") or request.POST.get("weight_step") or ""
        try:
            step = parse_weight_step(raw)
        except ValueError as error:
            context = weight_step_context(exercise, error=str(error))
            return render(request, "workouts/_weight_step_choice.html", context)

        ExerciseSettings.objects.update_or_create(
            user=request.user, exercise=exercise, defaults={"weight_step": step}
        )
        # Значение только что записано — перечитывать его из базы незачем.
        exercise.weight_step = step
        context = weight_step_context(exercise, saved=True)
        return render(request, "workouts/_weight_step_choice.html", context)


class ExerciseMuscleGroupView(LoginRequiredMixin, View):
    """Группа мышц своего упражнения: чипы уже принятых значений плюс своё."""

    def post(self, request, pk):
        # Общее упражнение правит только админ, чужое личное — никто.
        exercise = get_object_or_404(editable_exercises(request.user), pk=pk)
        exercise.muscle_group = chosen_muscle_group(
            request.POST, facets_for(request.user).muscle_groups
        )
        exercise.save(update_fields=["muscle_group"])
        # Список спрашиваем ещё раз уже после сохранения: только что введённая
        # своя группа обязана появиться среди чипов, иначе она пропала бы из
        # выбора до перезагрузки страницы.
        return render(
            request,
            "workouts/_muscle_group_choice.html",
            {
                "exercise": exercise,
                "muscle_groups": facets_for(request.user).muscle_groups,
                "max_length": MUSCLE_GROUP_MAX_LENGTH,
                "can_edit": True,
                "saved": True,
            },
        )


class ExerciseEquipmentView(LoginRequiredMixin, View):
    """Снаряд своего упражнения — вторая ось справочника, зеркало группы мышц."""

    def post(self, request, pk):
        # Общее упражнение правит только админ, чужое личное — никто.
        exercise = get_object_or_404(editable_exercises(request.user), pk=pk)
        exercise.equipment = chosen_equipment(request.POST, facets_for(request.user).equipment)
        exercise.save(update_fields=["equipment"])
        return render(
            request,
            "workouts/_equipment_choice.html",
            {
                "exercise": exercise,
                "can_edit": True,
                **equipment_context(exercise, facets_for(request.user), saved=True),
            },
        )


class ExerciseMachineView(LoginRequiredMixin, View):
    """Окно «Тренажёр»: производитель и модель упражнения в одном месте —
    выбором из справочника (MachineBrand, MachineModel), общего и своего.

    Два шага в одной модалке: сначала производитель (GET без brand или
    ?step=brands), потом его модель (?brand=<id>). Есть тренажёр — окно сразу
    открывается на его производителе. Новый производитель или модель вводится
    названием: совпавшее имя значит «это он» (services.machine_*_for_name), у
    администратора — ещё «Своё / Общее».

    Упражнение — любое видимое, место — только своё, записи справочника —
    только видимые: чужое по прямому id даёт 404. Сохранение отвечает
    HX-Refresh: окно открывают со страницы упражнения, из шторки, из живого
    режима и с правки записанной — перезагрузка обслуживает все без адреса
    возврата в запросе.
    """

    template_name = "workouts/_exercise_machine_modal.html"

    def get(self, request, pk, location_pk):
        exercise, location = self.get_objects(request, pk, location_pk)
        machine = self.current(request, exercise, location)
        raw = request.GET.get("brand", "")
        if raw.isdecimal():
            brand = self.visible_brand(request, raw)
        elif machine and request.GET.get("step") != "brands":
            brand = machine.brand
        else:
            brand = None
        return self.modal(request, exercise, location, machine, brand)

    def post(self, request, pk, location_pk):
        exercise, location = self.get_objects(request, pk, location_pk)
        machine = self.current(request, exercise, location)
        action = request.POST.get("action", "")
        lookup = {"user": request.user, "exercise": exercise, "location": location}
        if action == "remove":
            ExerciseMachine.objects.filter(**lookup).delete()
            return self.refresh()
        if action == "brand_new":
            form = self.name_form(request, MACHINE_BRAND_MAX_LENGTH, prefix="brand")
            if not form.is_valid():
                return self.modal(request, exercise, location, machine, None, brand_form=form)
            brand = services.machine_brand_for_name(
                request.user, form.cleaned_data["name"], shared=form.is_global
            )
            # Производитель выбран — дальше его модель; сохранять рано.
            return self.modal(request, exercise, location, machine, brand)
        brand = self.visible_brand(request, request.POST.get("brand", ""))
        if action == "model_new":
            form = self.name_form(request, MACHINE_MODEL_MAX_LENGTH, prefix="model")
            if form.is_valid():
                try:
                    model = services.machine_model_for_name(
                        request.user, brand, form.cleaned_data["name"], shared=form.is_global
                    )
                except ValueError as error:
                    form.add_error("name", str(error))
            if form.errors:
                return self.modal(request, exercise, location, machine, brand, model_form=form)
        else:
            raw = request.POST.get("model", "")
            model = None
            if raw:
                model = get_object_or_404(
                    MachineModel.objects.visible_to(request.user).filter(brand=brand),
                    pk=raw if raw.isdecimal() else 0,
                )
        ExerciseMachine.objects.update_or_create(
            **lookup, defaults={"brand": brand, "model": model}
        )
        return self.refresh()

    def get_objects(self, request, pk, location_pk):
        exercise = get_object_or_404(Exercise.objects.visible_to(request.user), pk=pk)
        location = get_object_or_404(Location.objects.filter(owner=request.user), pk=location_pk)
        return exercise, location

    def current(self, request, exercise, location):
        return (
            ExerciseMachine.objects.filter(user=request.user, exercise=exercise, location=location)
            .select_related("brand", "model")
            .first()
        )

    def visible_brand(self, request, raw):
        # Чужой свой производитель по прямому id — 404, как чужое место.
        return get_object_or_404(
            MachineBrand.objects.visible_to(request.user), pk=raw if raw.isdecimal() else 0
        )

    def name_form(self, request, max_length, *, prefix):
        return MachineNameForm(
            request.POST, user=request.user, max_length=max_length, prefix=prefix
        )

    def refresh(self):
        response = HttpResponse()
        response["HX-Refresh"] = "true"
        return response

    def modal(
        self, request, exercise, location, machine, brand, *, brand_form=None, model_form=None
    ):
        user = request.user
        context = {
            "exercise": exercise,
            "location": location,
            "machine": machine,
            "brand": brand,
            "is_admin": user.is_admin,
            "is_machine": is_machine(exercise),
        }
        if brand is None:
            context["brands"] = MachineBrand.objects.visible_to(user)
            context["form"] = brand_form or MachineNameForm(
                user=user, max_length=MACHINE_BRAND_MAX_LENGTH, prefix="brand"
            )
        else:
            context["models"] = MachineModel.objects.visible_to(user).filter(brand=brand)
            context["form"] = model_form or MachineNameForm(
                user=user, max_length=MACHINE_MODEL_MAX_LENGTH, prefix="model"
            )
        return render(request, self.template_name, context)


class ExerciseCreateView(LoginRequiredMixin, View):
    """«Создать» в справочнике: модалка с названием, единицей, группой и снарядом.

    Поля — те же, что у создания в живом режиме (_exercise_new_fields.html), но
    совпавшее имя здесь ошибка формы (ExerciseCreateForm). Успех — HX-Redirect
    на страницу нового упражнения: там его сразу видно и можно настроить шаг.
    """

    template_name = "workouts/_exercise_create_modal.html"

    def get(self, request):
        return self.modal(request, ExerciseCreateForm(user=request.user))

    def post(self, request):
        form = ExerciseCreateForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                with transaction.atomic():
                    exercise = form.save()
            except IntegrityError:
                # Параллельная вкладка успела завести то же имя между проверкой и INSERT.
                form.add_error("name", "Такое упражнение уже есть в справочнике.")
            else:
                url = reverse("exercise_detail", args=[exercise.pk])
                return HttpResponse(headers={"HX-Redirect": url})
        return self.modal(request, form)

    def modal(self, request, form):
        facets = facets_for(request.user)
        data = form.data
        chosen = data.get("measurement", "")
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "name": data.get("name", ""),
                "scope": data.get("scope") or ExerciseCreateForm.SCOPE_OWN,
                "exercise_max_length": EXERCISE_NAME_MAX_LENGTH,
                "measurement_choices": Exercise.Measurement.choices,
                "selected_measurement": (
                    chosen
                    if chosen in Exercise.Measurement.values
                    else Exercise.Measurement.WEIGHT_REPS
                ),
                "muscle_groups": facets.muscle_groups,
                "selected_muscle_group": chosen_facet_value(request, "muscle_group").strip(),
                "muscle_group_max_length": MUSCLE_GROUP_MAX_LENGTH,
                "equipment_list": facets.equipment,
                "selected_equipment": chosen_facet_value(request, "equipment").strip(),
                "equipment_max_length": EQUIPMENT_MAX_LENGTH,
            },
        )


class ExerciseRenameView(LoginRequiredMixin, View):
    """Переименование своего упражнения: опечатка правится один раз на всю историю.

    Зеркало LocationRenameView — и по той же причине: это та же строка БД,
    поэтому подходы, рекорды и график остаются на месте. Общее упражнение
    переименовывает администратор — новое имя увидят все.
    """

    def get_object(self):
        # Чужое по прямому URL — 404; общее — 404 всем, кроме администратора.
        return get_object_or_404(editable_exercises(self.request.user), pk=self.kwargs["pk"])

    def get(self, request, pk):
        return self.render_modal(self.get_object(), in_panel=bool(request.GET.get("panel")))

    def post(self, request, pk):
        exercise = self.get_object()
        # Режим отрисовки приезжает из формы: запрос из модалки всегда
        # HTMX-запрос, и HX-Request здесь уже ничего не различает.
        in_panel = bool(request.POST.get("panel"))
        name = collapse_spaces(request.POST.get("name", ""))[:EXERCISE_NAME_MAX_LENGTH]
        if not name:
            return self.render_modal(exercise, error="Введите название.", in_panel=in_panel)
        # Занятость считаем по всему видимому справочнику, а не только по своим:
        # переименование в имя глобального упражнения дало бы в списке две
        # одинаковые строки, и различить их было бы нечем.
        # У общего — ещё и среди всех общих: их уникальность держит база
        # (unique_global_exercise_name), и без проверки вышла бы 500.
        same_name = Exercise.objects.visible_to(request.user)
        if exercise.is_global:
            same_name = same_name | Exercise.objects.global_only()
        taken = same_name.filter(name__iexact=name).exclude(pk=exercise.pk).exists()
        if taken:
            return self.render_modal(
                exercise, error="Упражнение с таким названием уже есть.", in_panel=in_panel
            )
        exercise.name = name
        exercise.save(update_fields=["name"])
        # Отвечаем перерисованным телом страницы: оно свапает само себя
        # (hx-target="#exercise-panel" на мобильном — весь экран), поэтому в
        # #modal попадает пустой остаток и модалка закрывается сама.
        exercise = visible_exercise_with_step(request.user, exercise.pk)
        context = exercise_detail_context(request, exercise, in_panel=in_panel) | {"oob": True}
        return render(request, "workouts/_exercise_detail_body.html", context)

    def render_modal(self, exercise, *, error="", in_panel=False):
        return render(
            self.request,
            "workouts/_exercise_rename_modal.html",
            {
                "exercise": exercise,
                "exercise_max_length": EXERCISE_NAME_MAX_LENGTH,
                "error": error,
                "in_panel": in_panel,
            },
        )


class ExerciseListView(LoginRequiredMixin, ListView):
    """Каталог упражнений: глобальные и свои, с моими рекордами и поиском."""

    template_name = "workouts/exercise_list.html"
    context_object_name = "exercises"
    extra_context = {"nav_active": "exercises"}

    def get_queryset(self):
        # Священное правило: глобальные записи плюс свои, чужие личные не видны.
        queryset = Exercise.objects.visible_to(self.request.user)
        if self.request.GET.get("mine"):
            queryset = queryset.filter(owner=self.request.user)
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(name__icontains=query)
        group = self.chosen_group()
        if group:
            queryset = queryset.filter(muscle_group__iexact=group)
        equipment = self.chosen_equipment()
        if equipment:
            queryset = queryset.filter(equipment__iexact=equipment)
        # Счётчик использований нужен и подписи строки, и делению на «я тренирую»
        # / «остальное». Подпись говорит «в N тренировках», поэтому считаем
        # записанные: плановые подходы черновика тренировками ещё не стали.
        # Условие одно на оба агрегата (и на выгрузку справочника): две
        # скопированные руками копии со временем разъедутся, а дата обязана
        # совпадать с тем, что посчитал счётчик.
        mine = exercise_usage(self.request.user)
        # Оба агрегата идут по одному пути sets__workout, поэтому джойн один и тот
        # же, условия уезжают в FILTER (WHERE ...), строки не размножаются и
        # DISTINCT внутри COUNT не задет — запрос по-прежнему один. Бюджет каталога
        # упёрт в потолок, и лишний запрос здесь сломал бы тест.
        return queryset.annotate(
            workouts_count=Count("sets__workout", distinct=True, filter=mine),
            last_workout_at=Max("sets__workout__started_at", filter=mine),
        ).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        my_records = stats.strength_records(self.request.user)
        records = {row["exercise_id"]: row["value_display"] for row in my_records}
        today = timezone.localdate()
        for exercise in context["exercises"]:
            # Рекорд приходит уже с единицей внутри: «83,75 кг», «12 повторов», «1:30».
            exercise.record_display = records.get(exercise.pk)
            exercise.usage_label = usage_label(exercise.workouts_count)
            # Единицу показываем только у непривычных упражнений: приписка «вес ×
            # повторы» к каждому из двух десятков глобальных была бы шумом.
            exercise.measurement_label = (
                None
                if exercise.measurement == Exercise.Measurement.WEIGHT_REPS
                else exercise.get_measurement_display().lower()
            )
            # Таблица на ПК: дата последней тренировки и вторая строка под
            # названием — снаряд, необычная единица и «моё». Склейка здесь, а не в
            # шаблоне: так не остаётся висячих разделителей у пропущенных частей.
            exercise.last_workout_label = last_workout_label(exercise.last_workout_at, today)
            exercise.table_meta = " · ".join(
                part
                for part in (
                    exercise.equipment,
                    exercise.measurement_label,
                    None if exercise.is_global else "моё",
                )
                if part
            )
        # Справочник ниже остаётся полным: выполненное упражнение показывается и
        # плиткой, и строкой в своей группе — иначе в «Груди» не оказалось бы жима
        # лёжа, и искать его глазами по группе стало бы бесполезно.
        groups = group_by_muscle(context["exercises"])
        context["query"] = self.request.GET.get("q", "").strip()
        context["mine_only"] = bool(self.request.GET.get("mine"))
        context["groups"] = groups
        context["muscle_groups"] = self.known_groups()
        context["equipment_list"] = self.facets().equipment
        context["group_filter"] = self.chosen_group()
        context["equipment_filter"] = self.chosen_equipment()
        filtered = bool(
            context["query"]
            or context["mine_only"]
            or context["group_filter"]
            # Снаряд обязан попасть в этот флаг наравне с остальными фильтрами:
            # иначе при выбранном чипе снаряда плитки «Я тренирую» остались бы на
            # экране и показывали бы не то, что в списке ниже.
            or context["equipment_filter"]
        )
        # Плитки — только на «чистом» экране. При поиске нужен один список
        # результатов, а не два места, по которым они раскиданы.
        context["trained"] = [] if filtered else trained_first(context["exercises"])
        context["trained_count"] = len(context["trained"])
        # Заголовки блоков печатаются только когда блоков действительно два:
        # одинокое «Весь справочник» над единственным списком — шум.
        context["shows_blocks"] = bool(context["trained"])
        # Заголовок группы избыточен только когда группа одна И её название уже
        # стоит в активном чипе: без фильтра одна группа могла остаться и сама по
        # себе, и тогда назвать её больше нечем.
        context["shows_group_titles"] = len(groups) > 1 or not context["group_filter"]
        # Таблица на ПК — те же упражнения в порядке выбранной колонки. Телефон
        # читает exercises/groups/trained выше, и sort их не трогает.
        field, desc = parse_catalog_sort(self.request.GET.get("sort"))
        context["table_rows"] = sort_catalog(context["exercises"], field, desc)
        context["columns"] = catalog_columns(field, desc)
        context["sort_param"] = catalog_sort_param(field, desc)
        direction = "по убыванию" if desc else "по возрастанию"
        context["sort_caption"] = f"Упражнения {CATALOG_SORTS[field].caption}, {direction}"
        return context

    def facets(self):
        """Обе оси справочника. Кешируем: их спрашивают и фильтры, и оба ряда чипов."""
        if not hasattr(self, "_facets"):
            self._facets = facets_for(self.request.user)
        return self._facets

    def known_groups(self):
        return self.facets().muscle_groups

    def chosen(self, param, known):
        """Значение фильтра — ровно в том написании, что лежит в данных.

        Сравнение регистронезависимое, а неизвестное значение просто игнорируется:
        чипы приходят из данных, и в открытой вкладке они могли устареть.
        """
        wanted = self.request.GET.get(param, "").strip().lower()
        if not wanted:
            return ""
        return next((value for value in known if value.lower() == wanted), "")

    def chosen_group(self):
        return self.chosen("group", self.known_groups())

    def chosen_equipment(self):
        return self.chosen("equipment", self.facets().equipment)


class MySportsView(LoginRequiredMixin, ListView):
    """Личные виды спорта: сколько тренировок записано и удаление."""

    template_name = "workouts/my_sports.html"
    context_object_name = "sports"
    extra_context = {"nav_active": "profile"}

    def get_queryset(self):
        return (
            Sport.objects.filter(owner=self.request.user)
            .annotate(
                workouts_count=Count(
                    "workouts", distinct=True, filter=Q(workouts__duration_min__isnull=False)
                ),
                # Вид спорта, которым была только кардио-часть внутри силовой,
                # иначе выглядел бы неиспользованным — а удалиться не смог бы:
                # PROTECT держит его этой самой частью.
                parts_count=Count(
                    "cardio_parts",
                    distinct=True,
                    filter=Q(cardio_parts__workout__duration_min__isnull=False),
                ),
            )
            .order_by("name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for sport in context["sports"]:
            sport.usage_label = usage_label(sport.workouts_count + sport.parts_count)
        return context


def my_locations(user):
    """Места пользователя со счётчиком использований и готовой подписью.

    Один запрос на весь список: usage_label по каждому месту отдельным COUNT'ом
    дал бы N+1 на экране, который целиком про перечисление.
    """
    rows = list(
        Location.objects.filter(owner=user)
        .annotate(
            workouts_count=Count(
                "workouts",
                distinct=True,
                # Оба условия — в одном filter: два подряд дали бы два JOIN'а,
                # то есть «есть моя тренировка И есть чья-то записанная».
                # Свой user обязателен, хотя чужую тренировку к моему месту
                # приложение и не привяжет: подпись говорит про мои тренировки.
                filter=Q(workouts__user=user, workouts__duration_min__isnull=False),
            )
        )
        # Сортировку задаём явно: аннотация добавляет GROUP BY, а с ним Django
        # игнорирует Meta.ordering — и место по умолчанию не всплывало наверх
        # после смены звезды.
        .order_by("-is_default", "name")
    )
    for row in rows:
        row.usage_label = usage_label(row.workouts_count)
    return rows


def locations_context(user, *, error="", name=""):
    """Контекст экрана «Мои места». Ошибка и введённое имя переживают перерисовку."""
    return {
        "locations": my_locations(user),
        "location_max_length": LOCATION_NAME_MAX_LENGTH,
        "error": error,
        "name": name,
        "nav_active": "profile",
    }


class MyLocationsView(LoginRequiredMixin, View):
    """Мои места: добавление, выбор места по умолчанию, переименование, удаление.

    Экран холодный, поэтому добавление — обычный POST с редиректом (PRG), а не
    HTMX: без JS он обязан работать целиком.
    """

    def get(self, request):
        return render(request, "workouts/my_locations.html", locations_context(request.user))

    def post(self, request):
        name = collapse_spaces(request.POST.get("name", ""))[:LOCATION_NAME_MAX_LENGTH]
        if not name:
            return render(
                request,
                "workouts/my_locations.html",
                locations_context(request.user, error="Введите название."),
            )
        existing = Location.objects.filter(owner=request.user, name__iexact=name).first()
        location = services.location_for_name(request.user, name)
        if existing is not None:
            # Не ошибка формы: человек ввёл название места, которое у него есть,
            # и правильный ответ — «оно уже здесь», а не красная подсветка поля.
            messages.info(request, f"Место «{location.name}» уже есть.")
        else:
            messages.success(request, "Место добавлено.")
        return redirect("my_locations")


class LocationDefaultView(LoginRequiredMixin, View):
    """Место по умолчанию: тап по активному снимает его.

    Дефолтов не больше одного — это держит частичный уникальный индекс. Он
    проверяется немедленно и построчно (deferrable с condition Django
    запрещает), поэтому «снять у всех» и «поставить одному» — два отдельных
    UPDATE'а, а не один оператор с CASE.
    """

    def post(self, request, pk):
        with transaction.atomic():
            # Блокируем свои места и читаем состояние уже под блокировкой: без
            # этого вторая вкладка не увидела бы только что назначенный дефолт
            # (READ COMMITTED даёт ей снимок до чужого коммита) и упёрлась бы в
            # индекс с 500-й. order_by — против взаимной блокировки вкладок.
            mine = {
                row.pk: row
                for row in Location.objects.select_for_update()
                .filter(owner=request.user)
                .order_by("pk")
            }
            location = mine.get(pk)
            if location is None:
                raise Http404("Место не найдено")
            wanted = not location.is_default
            Location.objects.filter(owner=request.user, is_default=True).update(is_default=False)
            if wanted:
                Location.objects.filter(pk=pk).update(is_default=True)
        # Перерисовываем блок строк целиком: у старого дефолта надо снять
        # подпись, у нового поставить, и это проще, чем вести учёт, какая
        # строка была дефолтной.
        return render(request, "workouts/_location_rows.html", locations_context(request.user))


class LocationRenameView(LoginRequiredMixin, View):
    """Переименование места: опечатка правится один раз, история чинится вся.

    Ради этого место и стало моделью, а не текстовым полем тренировки.
    """

    def get_object(self):
        # Чужое место по прямому URL — 404.
        return get_object_or_404(
            Location.objects.filter(owner=self.request.user), pk=self.kwargs["pk"]
        )

    def get(self, request, pk):
        return self.render_modal(self.get_object())

    def post(self, request, pk):
        location = self.get_object()
        name = collapse_spaces(request.POST.get("name", ""))[:LOCATION_NAME_MAX_LENGTH]
        if not name:
            return self.render_modal(location, error="Введите название.")
        taken = (
            Location.objects.filter(owner=request.user, name__iexact=name)
            .exclude(pk=location.pk)
            .exists()
        )
        if taken:
            return self.render_modal(location, error="Место с таким названием у вас уже есть.")
        location.name = name
        location.save(update_fields=["name"])
        # Список приходит out-of-band, поэтому в #modal попадает пустой остаток
        # ответа и модалка закрывается сама — приём SportCreateView.
        return render(
            request,
            "workouts/_location_rows.html",
            locations_context(request.user) | {"oob": True},
        )

    def render_modal(self, location, error=""):
        return render(
            self.request,
            "workouts/_location_rename_modal.html",
            {
                "location": location,
                "location_max_length": LOCATION_NAME_MAX_LENGTH,
                "error": error,
            },
        )


class CatalogDeleteView(LoginRequiredMixin, View):
    """Удаление своей записи справочника: подтверждение страницей, удаление POST'ом.

    Использованную запись защищает база (on_delete=PROTECT). Проверяем это до
    удаления — чтобы дать понятное сообщение и спрятать кнопку — и на всякий
    случай ловим ProtectedError в savepoint: между проверкой и DELETE другая
    вкладка может записать подход, а при ATOMIC_REQUESTS исключение стоило бы
    500 и откат всей транзакции запроса.
    """

    model = None
    title = ""
    in_use_message = ""
    # Ссылка из черновика тоже держит запись (FK PROTECT), но «записанной
    # тренировкой» она не является — иначе сообщение врало бы.
    planned_use_message = ""
    deleted_message = ""
    success_url_name = ""

    def editable_queryset(self):
        """Что можно удалить. Упражнения расширяют: админ удаляет и общие."""
        return self.model.objects.filter(owner=self.request.user)

    def get_object(self):
        # Чужая и глобальная запись по прямому URL — 404.
        return get_object_or_404(self.editable_queryset(), pk=self.kwargs["pk"])

    def referencing_workouts(self, item):
        """Тренировки, которые держат запись. Подклассы знают путь до них."""
        raise NotImplementedError

    def usage_counts(self, item):
        """Сколько записанных тренировок и сколько незавершённых держат запись.

        «Сколько раз использовано» и «можно ли удалить» — разные вопросы: подпись
        считает записанные, а удаление блокирует любая ссылка, включая черновик.
        Иначе вышел бы тупик: «не использовалось» рядом с кнопкой, которая падает.
        """
        workouts = self.referencing_workouts(item)
        return workouts.finished().count(), workouts.unfinished().count()

    def blocked_message(self, item, recorded, unfinished):
        if recorded:
            return self.in_use_message.format(name=item.name)
        if unfinished:
            return self.planned_use_message.format(name=item.name)
        return ""

    def get(self, request, pk):
        item = self.get_object()
        recorded, unfinished = self.usage_counts(item)
        return render(
            request,
            "workouts/catalog_confirm_delete.html",
            {
                "item": item,
                "title": self.title,
                "usage_label": usage_label(recorded),
                "blocked_message": self.blocked_message(item, recorded, unfinished),
                "cancel_url": reverse(self.success_url_name),
                "nav_active": "profile",
            },
        )

    def post(self, request, pk):
        item = self.get_object()
        recorded, unfinished = self.usage_counts(item)
        blocked = self.blocked_message(item, recorded, unfinished)
        if blocked:
            messages.error(request, blocked)
            return redirect(self.success_url_name)
        try:
            with transaction.atomic():
                item.delete()
        except ProtectedError:
            messages.error(request, self.in_use_message.format(name=item.name))
            return redirect(self.success_url_name)
        messages.success(request, self.deleted_message)
        return redirect(self.success_url_name)


class ExerciseDeleteView(CatalogDeleteView):
    model = Exercise
    title = "Удалить упражнение?"
    in_use_message = "Упражнение «{name}» есть в записанных тренировках — его нельзя удалить."
    planned_use_message = (
        "Упражнение «{name}» есть в подготовленной тренировке — сначала уберите его оттуда."
    )
    deleted_message = "Упражнение удалено."
    success_url_name = "exercise_list"

    def editable_queryset(self):
        # Общее упражнение удаляет администратор — и только неиспользованное:
        # тренировки всех пользователей держат его так же, как свои (PROTECT).
        return editable_exercises(self.request.user)

    def referencing_workouts(self, item):
        # distinct: в одной тренировке у упражнения несколько подходов.
        return Workout.objects.filter(sets__exercise=item).distinct()


class SportDeleteView(CatalogDeleteView):
    model = Sport
    title = "Удалить вид спорта?"
    in_use_message = "Вид спорта «{name}» есть в записанных тренировках — его нельзя удалить."
    planned_use_message = (
        "Вид спорта «{name}» есть в подготовленной тренировке — сначала удалите черновик."
    )
    deleted_message = "Вид спорта удалён."
    success_url_name = "my_sports"

    def referencing_workouts(self, item):
        return Workout.objects.filter(sport=item)


class LocationDeleteView(CatalogDeleteView):
    model = Location
    title = "Удалить место?"
    in_use_message = "Место «{name}» есть в записанных тренировках — его нельзя удалить."
    planned_use_message = (
        "Место «{name}» есть в подготовленной тренировке — сначала удалите черновик."
    )
    deleted_message = "Место удалено."
    success_url_name = "my_locations"

    def referencing_workouts(self, item):
        return Workout.objects.filter(location=item)


MACHINE_KINDS = {"brand": MachineBrand, "model": MachineModel}


def machine_uses_label(count):
    """«у 2 упражнений» или «не используется» — подпись записи справочника тренажёров."""
    if not count:
        return "не используется"
    word = ru_plural(count, "упражнения", "упражнений", "упражнений")
    return f"у {count} {word}"


def label_machine_uses(items):
    """Подписи «у N упражнений» — производителям и их моделям из prefetch."""
    items = list(items)
    for item in items:
        item.uses_label = machine_uses_label(item.uses)
        for model in getattr(item, "own_models", ()):
            model.uses_label = machine_uses_label(model.uses)
    return items


def editable_machine_items(user, model):
    """Что человек правит на «Моих тренажёрах»: своё, а администратор — и общее."""
    items = model.objects.filter(owner=user)
    if user.is_admin:
        items = model.objects.filter(Q(owner=user) | Q(owner__isnull=True))
    return items


class MyMachinesView(LoginRequiredMixin, TemplateView):
    """«Мои тренажёры»: свои производители и модели — переименовать и удалить.

    Свои модели общих производителей — отдельной группой: удалить общего
    производителя человек не может, а свою модель у него — может. Администратор
    ниже видит общий список с теми же действиями. Число использований — числом
    тренажёров упражнений (любого пользователя: им держится удаление),
    аннотацией в тех же запросах.
    """

    template_name = "workouts/my_machines.html"

    def get_context_data(self, **kwargs):
        user = self.request.user
        own_models = with_request_status(
            MachineModel.objects.filter(owner=user).annotate(uses=Count("exercise_machines")),
            "model",
        )
        own_brands = (
            with_request_status(MachineBrand.objects.filter(owner=user), "brand")
            .annotate(uses=Count("exercise_machines"))
            .prefetch_related(Prefetch("models", queryset=own_models, to_attr="own_models"))
        )
        # Свои модели общих производителей — по производителю, в порядке названий.
        foreign = {}
        for item in own_models.filter(brand__owner__isnull=True).select_related("brand"):
            foreign.setdefault(item.brand, []).append(item)
        own_brands = label_machine_uses(own_brands)
        for items in foreign.values():
            label_machine_uses(items)
        context = {
            "own_brands": own_brands,
            "own_models_of_shared": sorted(foreign.items(), key=lambda pair: pair[0].name),
            # Действие у своих записей: администратор делает общим сам,
            # остальные предлагают заявкой.
            "share_action": "global" if user.is_admin else "propose",
            "nav_active": "profile",
        }
        if user.is_admin:
            shared_models = (
                MachineModel.objects.global_only()
                .select_related("contributed_by")
                .annotate(uses=Count("exercise_machines"))
            )
            context["show_shared"] = True
            context["shared_brands"] = label_machine_uses(
                MachineBrand.objects.global_only()
                .select_related("contributed_by")
                .annotate(uses=Count("exercise_machines"))
                .prefetch_related(Prefetch("models", queryset=shared_models, to_attr="own_models"))
            )
        return super().get_context_data(**kwargs) | context


class MachineCreateView(LoginRequiredMixin, View):
    """«Создать» на «Моих тренажёрах»: новый производитель или модель (kind).

    Вид записи — в адресе, переключают его чипы окна; ?brand=<id> заранее
    выбирает производителя у «+ Модель» в группе, ?scope=global — «Общее» у
    администратора в общем списке. Успех — HX-Refresh: запись встаёт в свою
    группу страницы.
    """

    template_name = "workouts/_machine_create_modal.html"

    def get(self, request, kind):
        initial = {"brand": request.GET.get("brand", ""), "scope": request.GET.get("scope", "")}
        return self.modal(request, self.form(initial=initial))

    def post(self, request, kind):
        form = self.form(data=request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
            except IntegrityError:
                # Двойная отправка: запись уже завела первая.
                form.add_error("name", "Такое название уже есть.")
            else:
                response = HttpResponse()
                response["HX-Refresh"] = "true"
                return response
        return self.modal(request, form)

    def form(self, **kwargs):
        if self.kwargs["kind"] not in MACHINE_KINDS:
            raise Http404
        return MachineCreateForm(
            user=self.request.user, kind=self.kwargs["kind"], prefix="new", **kwargs
        )

    def modal(self, request, form):
        context = {"form": form, "kind": self.kwargs["kind"]}
        if "brand" in form.fields:
            context["brands"] = form.fields["brand"].queryset
        return render(request, self.template_name, context)


class MakeGlobalView(LoginRequiredMixin, View):
    """«Сделать общим» у администратора: своё упражнение, производитель или модель.

    GET — окно подтверждения, POST — перевод (contributions.make_global): та же
    строка меняет владельца, поэтому свои тренировки и тренажёры админа с ней
    остаются. Не-администратору, чужое и уже общее — 404. Общая с тем же
    названием — то же окно с текстом и ссылкой на неё. Успех — HX-Refresh:
    окно открывают и со страницы упражнения (в том числе из шторки), и с «Моих
    тренажёров».
    """

    template_name = "workouts/_make_global_modal.html"

    def get(self, request, **kwargs):
        return self.modal(request, self.get_item())

    def post(self, request, **kwargs):
        item = self.get_item()
        try:
            contributions.make_global(item)
        except contributions.DuplicateError as error:
            return self.modal(request, item, error=error)
        response = HttpResponse()
        response["HX-Refresh"] = "true"
        return response

    def kind(self):
        return self.kwargs.get("kind", "exercise")

    def get_item(self):
        model = {"exercise": Exercise, **MACHINE_KINDS}.get(self.kind())
        if model is None or not self.request.user.is_admin:
            raise Http404
        items = model.objects.filter(owner=self.request.user)
        if model is MachineModel:
            items = items.select_related("brand")
        return get_object_or_404(items, pk=self.kwargs["pk"])

    def modal(self, request, item, *, error=None):
        kind = self.kind()
        if kind == "exercise":
            post_url = reverse("exercise_make_global", args=[item.pk])
        else:
            post_url = reverse("machine_make_global", args=[kind, item.pk])
        twin_url = ""
        if error is not None and isinstance(error.existing, Exercise):
            twin_url = reverse("exercise_detail", args=[error.existing.pk])
        return render(
            request,
            self.template_name,
            {
                "item": item,
                "kind": kind,
                "post_url": post_url,
                "error": error,
                "twin_url": twin_url,
            },
        )


CATALOG_KINDS = {"exercise": Exercise, **MACHINE_KINDS}


def latest_request(queryset_field):
    """Последняя заявка на запись — подзапросом к строке справочника.

    Своё видит только владелец, поэтому и заявки на него — только его.
    """
    return CatalogRequest.objects.filter(**{queryset_field: OuterRef("pk")}).order_by(
        "-created_at", "-pk"
    )


def with_request_status(queryset, field):
    """request_status и request_reason последней заявки — без запроса на строку."""
    latest = latest_request(field)
    return queryset.annotate(
        request_status=Subquery(latest.values("status")[:1]),
        request_reason=Subquery(latest.values("reason")[:1]),
    )


class CatalogProposeView(LoginRequiredMixin, View):
    """«Предложить в общий справочник»: окно с комментарием и заявка.

    Только своё и только не-администратору: у администратора на том же месте
    «Сделать общим». Ожидающая заявка на запись уже есть — то же окно с
    текстом. Админам уходит письмо; ответ — HX-Refresh: окно открывают со
    страницы упражнения и с «Моих тренажёров».
    """

    template_name = "workouts/_catalog_propose_modal.html"

    def get(self, request, kind, pk):
        return self.modal(request, self.get_item(), CatalogRequestForm())

    def post(self, request, kind, pk):
        item = self.get_item()
        form = CatalogRequestForm(request.POST)
        if form.is_valid():
            try:
                req = contributions.propose(request.user, item, form.cleaned_data["comment"])
            except ValueError as error:
                form.add_error(None, str(error))
            else:
                request_notify.new_request(request, req)
                response = HttpResponse()
                response["HX-Refresh"] = "true"
                return response
        return self.modal(request, item, form)

    def get_item(self):
        model = CATALOG_KINDS.get(self.kwargs["kind"])
        if model is None or self.request.user.is_admin:
            raise Http404
        items = model.objects.filter(owner=self.request.user)
        if model is MachineModel:
            items = items.select_related("brand")
        return get_object_or_404(items, pk=self.kwargs["pk"])

    def modal(self, request, item, form):
        context = {"item": item, "kind": self.kwargs["kind"], "form": form}
        return render(request, self.template_name, context)


class CatalogWithdrawView(LoginRequiredMixin, View):
    """«Отозвать» — своя ожидающая заявка удаляется: решать больше нечего."""

    def post(self, request, pk):
        req = get_object_or_404(
            CatalogRequest.objects.filter(user=request.user, status=CatalogRequest.Status.PENDING),
            pk=pk,
        )
        req.delete()
        response = HttpResponse()
        response["HX-Refresh"] = "true"
        return response


class MyRequestsView(LoginRequiredMixin, TemplateView):
    """«Мои заявки»: свои заявки в общий справочник со статусом и причиной отказа."""

    template_name = "workouts/my_requests.html"

    def get_context_data(self, **kwargs):
        items = CatalogRequest.objects.filter(user=self.request.user).select_related(
            "exercise", "brand", "model__brand"
        )
        return super().get_context_data(**kwargs) | {"items": items, "nav_active": "profile"}


class MachineItemMixin(LoginRequiredMixin):
    """Запись справочника тренажёров по kind (brand | model) и pk.

    Чужая запись — 404, общая — 404 всем, кроме администратора.
    """

    def get_item(self):
        model = MACHINE_KINDS.get(self.kwargs["kind"])
        if model is None:
            raise Http404
        items = editable_machine_items(self.request.user, model)
        if model is MachineModel:
            items = items.select_related("brand")
        return get_object_or_404(items, pk=self.kwargs["pk"])


class MachineRenameView(MachineItemMixin, View):
    """Переименование производителя или модели. Занятое имя — ошибка формы.

    Тренажёры упражнений ссылаются на запись, поэтому новое имя сразу видно
    везде — в живом режиме, итоге и истории (как у переименованного места).
    """

    template_name = "workouts/_machine_rename_modal.html"

    def get(self, request, kind, pk):
        item = self.get_item()
        form = self.form(initial={"name": item.name})
        return self.modal(request, item, form)

    def post(self, request, kind, pk):
        item = self.get_item()
        form = self.form(data=request.POST)
        if form.is_valid():
            name = form.cleaned_data["name"]
            same = type(item).objects.filter(owner=item.owner, name__iexact=name)
            if isinstance(item, MachineModel):
                same = same.filter(brand=item.brand)
            if same.exclude(pk=item.pk).exists():
                form.add_error("name", "Такое название уже есть.")
            else:
                item.name = name
                item.save(update_fields=["name"])
                response = HttpResponse()
                response["HX-Refresh"] = "true"
                return response
        return self.modal(request, item, form)

    def form(self, **kwargs):
        model = MACHINE_KINDS[self.kwargs["kind"]]
        max_length = model._meta.get_field("name").max_length
        return MachineNameForm(
            user=self.request.user, max_length=max_length, with_scope=False, **kwargs
        )

    def modal(self, request, item, form):
        return render(
            request,
            self.template_name,
            {"item": item, "kind": self.kwargs["kind"], "form": form},
        )


class MachineDeleteView(MachineItemMixin, View):
    """Удаление производителя или модели: подтверждение страницей, удаление POST'ом.

    Запись, на которую ссылается чей-то тренажёр, не удаляется (RESTRICT держит
    это и в базе). Производитель уходит вместе со своими моделями того же
    владельца; если у общего производителя есть чужие личные модели, удалить его
    нельзя — они остались бы без производителя.
    """

    def blocked_message(self, item, uses):
        if uses:
            return f"«{item.name}» указан у тренажёров упражнений — сначала замените его там."
        if isinstance(item, MachineBrand) and self.foreign_models(item).exists():
            return f"У «{item.name}» есть модели других пользователей — его нельзя удалить."
        return ""

    def foreign_models(self, brand):
        models_ = MachineModel.objects.filter(brand=brand)
        if brand.owner_id is None:
            return models_.exclude(owner__isnull=True)
        return models_.exclude(owner=brand.owner)

    def uses(self, item):
        return ExerciseMachine.objects.filter(
            **{"brand" if isinstance(item, MachineBrand) else "model": item}
        ).count()

    def get(self, request, kind, pk):
        item = self.get_item()
        uses = self.uses(item)
        note = ""
        if isinstance(item, MachineBrand):
            count = MachineModel.objects.filter(brand=item, owner=item.owner).count()
            if count:
                note = f"Вместе с ним удалятся модели: {count}."
        return render(
            request,
            "workouts/catalog_confirm_delete.html",
            {
                "item": item,
                "title": "Удалить производителя?" if kind == "brand" else "Удалить модель?",
                "usage_label": machine_uses_label(uses),
                "blocked_message": self.blocked_message(item, uses),
                "delete_note": note,
                "cancel_url": reverse("my_machines"),
                "nav_active": "profile",
            },
        )

    def post(self, request, kind, pk):
        item = self.get_item()
        blocked = self.blocked_message(item, self.uses(item))
        if blocked:
            messages.error(request, blocked)
            return redirect("my_machines")
        try:
            with transaction.atomic():
                if isinstance(item, MachineBrand):
                    MachineModel.objects.filter(brand=item, owner=item.owner).delete()
                item.delete()
        except (ProtectedError, RestrictedError):
            messages.error(request, f"«{item.name}» уже используется — его нельзя удалить.")
            return redirect("my_machines")
        messages.success(request, "Удалено.")
        return redirect("my_machines")


def decorate_news(entry, user, today):
    """Подпись даты и метка «Новое» карточки новости.

    Одна на страницу и на ответ отметки «прочитано»: карточка в обоих местах
    обязана выглядеть одинаково. Ждёт пометку is_read (with_read_mark).
    """
    moment = timezone.localtime(entry.published_at)
    entry.date_label = formats.date_format(moment, "j E" if moment.year == today.year else "j E Y")
    entry.is_new = entry.is_new_for(user)
    return entry


class ChangelogView(LoginRequiredMixin, View):
    """«Что нового». Открытие страницы гасит точку-бейдж в профиле.

    GET меняет состояние осознанно: это обычный «прочитано при открытии» —
    пишется одна колонка своей же строки, повторные открытия просто сдвигают
    отметку вперёд, а при ошибке рендера транзакция откатится и новости
    останутся непрочитанными. Метка «Новое» у карточек — другое: она гаснет по
    нажатию на конкретную новость (ChangelogReadView).
    """

    def get(self, request):
        today = timezone.localdate()
        entries = [
            decorate_news(entry, request.user, today)
            for entry in ChangelogEntry.objects.published().with_read_mark(request.user)
        ]
        request.user.changelog_seen_at = timezone.now()
        request.user.save(update_fields=["changelog_seen_at"])
        # Точка в панели гаснет на этой же странице: ленивые бейджи
        # (accounts.context_processors.sidebar_badges) считаются при рендере,
        # уже после записи changelog_seen_at.
        return render(
            request,
            "workouts/changelog.html",
            {"entries": entries, "nav_active": "profile"},
        )


class ChangelogReadView(LoginRequiredMixin, View):
    """Нажатие на карточку новости: «прочитано», метка «Новое» гаснет.

    Ответ — та же карточка уже без метки (outerHTML). Отметка у каждого своя:
    read_by.add(request.user), чужую поставить нельзя; add повторную пару не
    дублирует. Черновик и отложенная новость — 404, как на самой странице.
    """

    def post(self, request, pk):
        entry = get_object_or_404(ChangelogEntry.objects.published(), pk=pk)
        entry.read_by.add(request.user)
        entry.is_read = True
        decorate_news(entry, request.user, timezone.localdate())
        return render(request, "workouts/_changelog_entry.html", {"entry": entry})


# Префикс формы справочника: у двух форм загрузки на одной странице иначе был бы
# общий id поля, и подпись одной открывала бы выбор файла в другой.
EXERCISE_FORM_PREFIX = "exercises"


class DataTransferView(LoginRequiredMixin, View):
    """Экран обмена данными: история тренировок и справочник упражнений —
    выгрузка в файл и загрузка из файла. POST здесь — загрузка истории."""

    def get(self, request):
        return self.page(request)

    def post(self, request):
        """Загрузка книги. Ответ — та же страница с отчётом, без редиректа.

        Отчёт (что создано, что пропущено, где ошибки) в messages не влезает, а
        повторная отправка формы по F5 безвредна: тренировки, которые уже есть,
        уйдут в «пропущено».
        """
        form = XlsxUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.page(request, form=form)
        try:
            report = excel_import.import_workbook(request.user, form.cleaned_data["file"])
        except excel.WorkbookError as error:
            return self.page(request, error=str(error))
        return self.page(request, report=report)

    def page(self, request, **extra):
        context = self.page_context(request, **extra)
        return render(request, "workouts/data_transfer.html", context)

    def page_context(self, request, **extra):
        count = Workout.objects.filter(user=request.user).finished().count()
        return {
            "nav_active": "profile",
            "workouts_count": count,
            "workouts_label": ru_plural(count, "тренировка", "тренировки", "тренировок"),
            "form": XlsxUploadForm(),
            "exercise_form": XlsxUploadForm(prefix=EXERCISE_FORM_PREFIX),
        } | extra


class ExerciseImportView(DataTransferView):
    """Загрузка справочника упражнений.

    Отдельный адрес, а не скрытое поле в общей форме: подменой параметра нельзя
    загрузить справочник как историю и наоборот (приём cardio_prepare). Ответ —
    та же страница обмена с отчётом, как у истории; повтор по F5 безвреден —
    совпадающее не пишется. GET сюда не ходит: формы здесь нет, есть страница.
    """

    def get(self, request):
        return redirect("data_transfer")

    def post(self, request):
        form = XlsxUploadForm(request.POST, request.FILES, prefix=EXERCISE_FORM_PREFIX)
        if not form.is_valid():
            return self.page(request, exercise_form=form)
        try:
            report = exercise_excel.import_workbook(request.user, form.cleaned_data["file"])
        except excel.WorkbookError as error:
            return self.page(request, exercise_error=str(error))
        return self.page(request, exercise_report=report)


class WorkoutExportView(LoginRequiredMixin, View):
    """Выгрузка всей записанной истории одним файлом .xlsx.

    Книга собирается в памяти: файлов на диске проект не держит вовсе, а дневник
    на несколько лет — это сотни килобайт.
    """

    def get(self, request):
        buffer = io.BytesIO()
        excel.build_workbook(request.user).save(buffer)
        buffer.seek(0)
        # as_attachment вместо ручного Content-Disposition: Django сам кодирует
        # кириллицу в имени по RFC 5987, иначе файл сохранился бы как «_____.xlsx».
        return FileResponse(
            buffer,
            as_attachment=True,
            filename=excel.export_filename(timezone.localdate()),
            content_type=excel.CONTENT_TYPE,
        )


class ExerciseExportView(LoginRequiredMixin, View):
    """Выгрузка справочника упражнений одним файлом .xlsx: общие и свои.

    Отдельный файл, а не лист в книге истории: у справочника свой смысл строки,
    а колонки истории — договор, который ломать нельзя.
    """

    def get(self, request):
        buffer = io.BytesIO()
        exercise_excel.build_workbook(request.user).save(buffer)
        buffer.seek(0)
        return FileResponse(
            buffer,
            as_attachment=True,
            filename=exercise_excel.export_filename(timezone.localdate()),
            content_type=excel.CONTENT_TYPE,
        )


# ---------- Замеры тела ----------
#
# Журнал замеров с датами: вес, рост, обхваты и свои параметры. Параметры —
# гибридный справочник (общие + свои), замеры — личные. Изоляция фичи держится
# на user=request.user в каждом запросе замеров: строки общих параметров у всех
# одни и те же, а значения — у каждого свои.


def measure_day_label(day, today):
    """«сегодня», «вчера», «28 сен» — с годом, если он не текущий."""
    if day == today:
        return "сегодня"
    if day == today - timedelta(days=1):
        return "вчера"
    return formats.date_format(day, "j b" if day.year == today.year else "j b Y")


def body_metric_rows(user):
    """Видимые параметры с последним и предыдущим замером пользователя.

    Одним запросом: значения подзапросами, как шаг веса в with_weight_step.
    Фильтр user=user внутри подзапроса — то, что не даёт увидеть чужой вес на
    общем параметре.
    """
    mine = BodyMeasurement.objects.filter(user=user, metric=OuterRef("pk")).order_by("-measured_on")
    metrics = list(
        BodyMetric.objects.visible_to(user).annotate(
            last_value=Subquery(mine.values("value")[:1]),
            last_on=Subquery(mine.values("measured_on")[:1]),
            previous_value=Subquery(mine.values("value")[1:2]),
        )
    )
    today = timezone.localdate()
    for metric in metrics:
        if metric.last_on is not None:
            metric.last_display = measurement_display(metric.last_value, metric.unit)
            metric.last_label = measure_day_label(metric.last_on, today)
            metric.delta = measurement_delta(metric.last_value, metric.previous_value, metric.unit)
    return metrics


class BodyMeasurementsView(LoginRequiredMixin, View):
    """«Мои замеры»: параметры с последним значением, остальные — списком ниже.

    Сверху то, что человек уже измеряет, — со значением, днём и изменением к
    прошлому замеру; ниже остальные параметры строкой, чтобы новичок не видел
    восемь пустых карточек (приём «Я тренирую» каталога).
    """

    def get(self, request):
        metrics = body_metric_rows(request.user)
        return render(
            request,
            "workouts/body_measurements.html",
            {
                "tracked": [metric for metric in metrics if metric.last_on is not None],
                "untracked": [metric for metric in metrics if metric.last_on is None],
                "nav_active": "profile",
            },
        )


class BodyMetricView(LoginRequiredMixin, View):
    """Страница параметра: график, история замеров с изменениями, «+ Замер»."""

    def get(self, request, pk):
        metric = get_object_or_404(BodyMetric.objects.visible_to(request.user), pk=pk)
        entries = list(
            BodyMeasurement.objects.filter(user=request.user, metric=metric).order_by("measured_on")
        )
        today = timezone.localdate()
        previous = None
        for entry in entries:
            entry.display = measurement_display(entry.value, metric.unit)
            entry.delta = measurement_delta(entry.value, previous, metric.unit)
            entry.day_label = measure_day_label(entry.measured_on, today)
            previous = entry.value
        count = len(entries)
        return render(
            request,
            "workouts/body_metric.html",
            {
                "metric": metric,
                "history": entries[::-1],
                "count_label": (
                    f"{count} {ru_plural(count, 'замер', 'замера', 'замеров')}"
                    if count
                    else "замеров пока нет"
                ),
                # Только float: json_script сериализует Decimal строками, и
                # математика графика на клиенте сломалась бы молча.
                "chart": {
                    "labels": [f"{entry.measured_on:%d.%m}" for entry in entries],
                    "values": [float(entry.value) for entry in entries],
                    "unit": metric.unit,
                    "format": "",
                    "colorKey": "strength",
                },
                "can_edit": metric.owner_id == request.user.pk,
                "nav_active": "profile",
            },
        )


def body_redirect(back, metric):
    """Куда вернуться после модалки: на список или на страницу параметра.

    Цель приходит словом, а не адресом: подмена поля не уведёт на чужой сайт.
    """
    if back == "metric" and metric is not None:
        url = reverse("body_metric", args=[metric.pk])
    else:
        url = reverse("body_measurements")
    return HttpResponse(headers={"HX-Redirect": url})


class BodyMeasurementView(LoginRequiredMixin, View):
    """Модалка замера: добавить (без pk), поправить или удалить (с pk).

    Добавление за уже занятый день — исправление: значение перезаписывается,
    второй замер за день не появляется (UniqueConstraint). Ответ — HX-Redirect:
    после записи меняются и значение, и изменение, и график.
    """

    def get_entry(self, request, pk):
        if pk is None:
            return None
        return get_object_or_404(
            BodyMeasurement.objects.filter(user=request.user).select_related("metric"), pk=pk
        )

    def get(self, request, pk=None):
        entry = self.get_entry(request, pk)
        if entry is not None:
            form = BodyMeasurementForm(
                user=request.user,
                instance=entry,
                initial={"value": decimal_display(entry.value), "measured_on": entry.measured_on},
            )
            return self.modal(request, form, entry=entry, back="metric", unit=entry.metric.unit)
        # Параметр — из адреса (со страницы параметра) или первый видимый, то
        # есть вес: его меряют чаще всего.
        visible = BodyMetric.objects.visible_to(request.user)
        raw = request.GET.get("metric", "")
        chosen = visible.filter(pk=raw).first() if raw.isdecimal() else None
        chosen = chosen or visible.first()
        initial = {"measured_on": timezone.localdate(), "metric": chosen.pk if chosen else None}
        form = BodyMeasurementForm(user=request.user, initial=initial)
        return self.modal(
            request, form, back=request.GET.get("back", "list"), unit=chosen.unit if chosen else ""
        )

    def post(self, request, pk=None):
        entry = self.get_entry(request, pk)
        back = request.POST.get("back", "list")
        if entry is not None and request.POST.get("delete"):
            metric = entry.metric
            entry.delete()
            messages.success(request, "Замер удалён.")
            return body_redirect(back, metric)
        form = BodyMeasurementForm(request.POST, user=request.user, instance=entry)
        if not form.is_valid():
            unit = entry.metric.unit if entry else self.posted_unit(request)
            return self.modal(request, form, entry=entry, back=back, unit=unit)
        value = form.cleaned_data["value"]
        day = form.cleaned_data["measured_on"]
        if entry is not None:
            entry.value = value
            entry.measured_on = day
            entry.save(update_fields=["value", "measured_on"])
            metric = entry.metric
            messages.success(request, "Замер исправлен.")
        else:
            metric = form.cleaned_data["metric"]
            _, created = BodyMeasurement.objects.update_or_create(
                user=request.user, metric=metric, measured_on=day, defaults={"value": value}
            )
            if created:
                messages.success(request, "Замер записан.")
            else:
                label = formats.date_format(day, "j E")
                messages.success(request, f"Замер за {label} обновлён.")
        return body_redirect(back, metric)

    @staticmethod
    def posted_unit(request):
        """Единица выбранного в форме параметра — для подписи после ошибки."""
        raw = request.POST.get("metric", "")
        if not raw.isdecimal():
            return ""
        metric = BodyMetric.objects.visible_to(request.user).filter(pk=raw).first()
        return metric.unit if metric else ""

    def modal(self, request, form, *, entry=None, back="list", unit=""):
        return render(
            request,
            "workouts/_body_measurement_modal.html",
            {
                "form": form,
                "entry": entry,
                "back": "metric" if back == "metric" else "list",
                "unit": unit,
            },
        )


class BodyMetricEditView(LoginRequiredMixin, View):
    """Свой параметр: создать (без pk), переименовать или сменить единицу (с pk).

    Общий параметр так не правится — его меняет миграция, — поэтому правка
    ищет только среди своих (filter(owner=user)): общий и чужой дают 404, как у
    LocationRenameView.
    """

    def get_metric(self, request, pk):
        if pk is None:
            return None
        return get_object_or_404(BodyMetric.objects.filter(owner=request.user), pk=pk)

    def get(self, request, pk=None):
        metric = self.get_metric(request, pk)
        initial = {"name": metric.name, "unit": metric.unit} if metric else {}
        form = BodyMetricForm(user=request.user, instance=metric, initial=initial)
        return self.modal(request, form, metric)

    def post(self, request, pk=None):
        metric = self.get_metric(request, pk)
        form = BodyMetricForm(request.POST, user=request.user, instance=metric)
        if not form.is_valid():
            return self.modal(request, form, metric)
        saved = form.save()
        if metric is None:
            messages.success(request, f"Параметр «{saved.name}» добавлен — запишите первый замер.")
        return HttpResponse(headers={"HX-Redirect": reverse("body_metric", args=[saved.pk])})

    def modal(self, request, form, metric):
        return render(
            request,
            "workouts/_body_metric_modal.html",
            {
                "form": form,
                "metric": metric,
                "name_max_length": BODY_METRIC_NAME_MAX_LENGTH,
                "unit_max_length": BODY_METRIC_UNIT_MAX_LENGTH,
            },
        )


class BodyMetricDeleteView(LoginRequiredMixin, View):
    """Удаление своего параметра — вместе с его замерами, после подтверждения.

    Не CatalogDeleteView: тот считает тренировки и запрещает удалять занятое, а
    здесь замеры — часть самого параметра, и страница честно говорит, сколько их
    уйдёт. Замеры удаляются явно: у ссылки замера RESTRICT.
    """

    def get_metric(self, request, pk):
        return get_object_or_404(BodyMetric.objects.filter(owner=request.user), pk=pk)

    def get(self, request, pk):
        metric = self.get_metric(request, pk)
        count = metric.measurements.filter(user=request.user).count()
        return render(
            request,
            "workouts/catalog_confirm_delete.html",
            {
                "title": "Удалить параметр",
                "item": metric,
                "usage_label": (
                    f"{count} {ru_plural(count, 'замер', 'замера', 'замеров')}"
                    if count
                    else "замеров нет"
                ),
                "delete_note": "Вместе с параметром удалятся все его замеры." if count else "",
                "cancel_url": reverse("body_metric", args=[metric.pk]),
                "nav_active": "profile",
            },
        )

    def post(self, request, pk):
        metric = self.get_metric(request, pk)
        with transaction.atomic():
            metric.measurements.filter(user=request.user).delete()
            try:
                metric.delete()
            except RestrictedError:
                # Свой параметр замеряет только владелец, но параллельная вкладка
                # могла успеть записать замер между двумя удалениями.
                transaction.set_rollback(True)
                messages.error(request, "Не получилось: появился новый замер. Попробуйте ещё раз.")
                return redirect("body_metric", pk=metric.pk)
        messages.success(request, f"Параметр «{metric.name}» удалён.")
        return redirect("body_measurements")
