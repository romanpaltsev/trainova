"""Экраны тренировок: лента истории, ввод кардио, живой режим, личные справочники.

Каждый queryset пользовательских данных фильтруется по request.user — чужая запись
по прямому URL даёт 404.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.db.models.deletion import ProtectedError
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import formats, timezone
from django.views.generic import DeleteView, ListView, TemplateView, View

from workouts import services, stats
from workouts.forms import MAX_DURATION_HOURS, CardioWorkoutForm, ExerciseQuickForm, SportForm
from workouts.models import (
    DEFAULT_WEIGHT_STEP,
    LOCATION_NAME_MAX_LENGTH,
    MAX_WEIGHT_KG,
    MEASUREMENT_FIELDS,
    METRIC_LABELS,
    METRIC_UNITS,
    MUSCLE_GROUP_MAX_LENGTH,
    NOTE_MAX_LENGTH,
    REST_DELTAS,
    SET_LIMITS,
    SET_STEPS,
    TIME_MEASUREMENTS,
    WEIGHT_STEP_CHOICES,
    ChangelogEntry,
    Exercise,
    ExerciseNote,
    ExerciseSettings,
    Location,
    Sport,
    StrengthSet,
    Workout,
    chosen_muscle_group,
    clamp_rest_seconds,
    collapse_spaces,
    decimal_display,
    metric_display,
    muscle_groups_for,
    parse_field_value,
    parse_weight_step,
    rest_display,
    ru_plural,
    with_weight_step,
)
from workouts.stats import week_start, week_title

HISTORY_PAGE_SIZE = 10

# Шаги и границы значений подхода живут в модели (SET_STEPS, SET_LIMITS): по ним
# же подписаны кнопки и предсказывается значение на клиенте. Ключи там — поля
# модели, поэтому применимость поля к единице упражнения проверяется по
# MEASUREMENT_FIELDS без словаря-переводчика.
# Что обязано быть заполнено, чтобы подход считался выполненным: у весовых это
# повторы (вес 0 — «со своим весом»), у удержаний — время.
REQUIRED_FIELD = {
    Exercise.Measurement.WEIGHT_REPS: ("reps", "Укажите повторения."),
    Exercise.Measurement.REPS: ("reps", "Укажите повторения."),
    Exercise.Measurement.TIME: ("duration_sec", "Укажите время."),
    Exercise.Measurement.TIME_WEIGHT: ("duration_sec", "Укажите время."),
}
EXERCISE_RESULTS_LIMIT = 30
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
            # cardio — обратная OneToOne, тянется тем же запросом; location —
            # для подписи места на карточке, тоже без лишнего запроса
            .select_related("sport", "cardio", "location")
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
            queryset = queryset.filter(sport_id=int(sport_id))
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
        context["groups"] = self._group_by_week(context["workouts"])
        context["prev_week"] = self.request.GET.get("prev_week", "")
        context["last_week"] = context["groups"][-1]["key"] if context["groups"] else ""
        context["sport_filter"] = self.request.GET.get("sport", "")
        context["sports_used"] = (
            # Оба условия — в одном filter: два вызова подряд дали бы два JOIN'а,
            # то есть «есть моя тренировка И есть чья-то завершённая». Чип строится
            # только по записанным: у черновика карточек в ленте нет.
            Sport.objects.filter(
                workouts__user=self.request.user, workouts__duration_min__isnull=False
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
    """Создание и правка кардио-тренировки одной формой."""

    template_name = "workouts/cardio_form.html"

    def get_instance(self):
        if "pk" not in self.kwargs:
            return None
        workout = get_object_or_404(
            Workout.objects.select_related("sport"), pk=self.kwargs["pk"], user=self.request.user
        )
        if workout.sport.is_strength:
            # Экран правки силовой тренировки появится вместе с живым режимом.
            raise Http404("Правка силовой тренировки пока не поддерживается")
        return workout

    def get(self, request, **kwargs):
        instance = self.get_instance()
        form = CardioWorkoutForm(
            user=request.user, instance=instance, initial=self.preselected(request, instance)
        )
        return self.render_form(form, instance)

    @staticmethod
    def preselected(request, instance):
        """Вид спорта из чузера «+» (?sport=): подсказка, а не адрес.

        Чужой личный, силовой или мусорный id молча игнорируем — 404 здесь был бы
        грубостью, а расширить набор сохраняемых видов параметр всё равно не может:
        форма валидирует sport своим queryset'ом. На правке подсказка запрещена:
        переданный initial перебивает данные тренировки и подменил бы ей вид спорта.
        """
        sport_id = request.GET.get("sport", "")
        if instance is not None or not sport_id.isdecimal():
            return None
        pk = (
            Sport.objects.visible_to(request.user)
            .filter(category=Sport.Category.CARDIO, pk=int(sport_id))
            .values_list("pk", flat=True)
            .first()
        )
        return {"sport": pk} if pk is not None else None

    def post(self, request, **kwargs):
        instance = self.get_instance()
        form = CardioWorkoutForm(request.POST, user=request.user, instance=instance)
        if form.is_valid():
            workout = form.save()
            messages.success(
                request,
                "Тренировка обновлена." if instance else "Тренировка записана.",
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
                "nav_active": "history" if instance else "add",
            },
        )


class WorkoutDeleteView(LoginRequiredMixin, DeleteView):
    """Удаление своей тренировки или черновика с подтверждением."""

    template_name = "workouts/workout_confirm_delete.html"
    context_object_name = "workout"
    # Выставляется в form_valid до удаления: после него объекта в базе уже нет.
    planned = False

    def get_queryset(self):
        return Workout.objects.filter(user=self.request.user).select_related("sport")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.object.is_planned:
            # У черновика нет даты, поэтому в подзаголовке — состав, а не «когда».
            count = self.object.sets.values("exercise").distinct().count()
            context["plan_label"] = exercises_label(count)
        return context

    def get_success_url(self):
        # Черновика в истории нет — возвращаться туда после удаления бессмысленно.
        return reverse("dashboard") if self.planned else reverse("workout_history")

    def form_valid(self, form):
        # Запоминаем до удаления: после super() объект уже без строки в базе.
        self.planned = self.object.is_planned
        messages.success(
            self.request, "Черновик удалён." if self.planned else "Тренировка удалена."
        )
        return super().form_valid(form)


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


def live_workout_or_404(request, pk):
    """Своя незавершённая силовая — база всех действий живого режима.

    Черновик правится тем же набором эндпоинтов, что и идущая тренировка:
    подготовка — это и есть добавление упражнений и правка весов.
    """
    return get_object_or_404(
        Workout.objects.filter(user=request.user, sport__category=Sport.Category.STRENGTH)
        .unfinished()
        # user — для отдыха по умолчанию и подсказок, иначе он тянется отдельным
        # запросом; location — для строки места в шапке, тем же запросом
        .select_related("sport", "user", "location"),
        pk=pk,
    )


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


def live_rest_context(workout, *, autostart=False, oob=False):
    seconds = workout.effective_rest_seconds
    return {
        "workout": workout,
        "rest_seconds": seconds,
        "rest_display": rest_display(seconds),
        "autostart": autostart,
        "oob": oob,
    }


def live_region_response(request, workout, *, oob=False, restart_timer=False, error=None):
    """Регион упражнений; при выполненном подходе — плюс OOB-карточка отдыха.

    Карточка отдыха пересоздаётся только здесь: пересоздание перезапускает
    Alpine-таймер, поэтому обычные действия региона её не трогают.
    """
    context = services.live_context(workout) | {"oob": oob, "error": error}
    html = render_to_string("workouts/_live_exercises.html", context, request=request)
    if restart_timer:
        html += render_to_string(
            "workouts/_live_rest.html",
            live_rest_context(workout, autostart=True, oob=True),
            request=request,
        )
    return HttpResponse(html)


class WorkoutStartView(LoginRequiredMixin, View):
    """HTMX-модалка «+»: продолжить идущую, открыть черновик, начать или записать."""

    def get(self, request):
        # Один запрос на идущую и на черновики: вместе их единицы.
        rows = list(
            Workout.objects.filter(user=request.user)
            .unfinished()
            .select_related("sport")
            .annotate(exercises_count=Count("sets__exercise", distinct=True))
            # Явно: с GROUP BY Django игнорирует Meta.ordering, а у черновика нет даты.
            .order_by("-id")
        )
        live = next((row for row in rows if not row.is_planned), None)
        drafts = [row for row in rows if row.is_planned]
        for draft in drafts:
            draft.plan_label = exercises_label(draft.exercises_count)
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
            # Черновиков может быть сколько угодно: уникальный индекс требует начала,
            # поэтому ловить IntegrityError здесь не нужно.
            workout = Workout.objects.create(
                user=request.user,
                sport=sport,
                location=location,
                started_at=None,
                duration_min=None,
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
                Workout.objects.filter(pk=workout.pk, started_at__isnull=True).update(
                    started_at=timezone.now()
                )
        except IntegrityError:
            live = Workout.objects.filter(user=request.user).live().first()
            if live is None:
                raise
            messages.info(request, "Сначала завершите текущую тренировку.")
            return redirect("workout_live", pk=live.pk)
        return redirect("workout_live", pk=workout.pk)


class LiveWorkoutView(LoginRequiredMixin, View):
    """Экран живого режима силовой тренировки."""

    def get(self, request, pk):
        workout = get_object_or_404(
            Workout.objects.filter(user=request.user).select_related("sport", "user", "location"),
            pk=pk,
        )
        if not workout.sport.is_strength:
            raise Http404("Живой режим есть только у силовых тренировок")
        if workout.is_finished:
            return redirect("workout_summary", pk=workout.pk)
        context = services.live_context(workout) | live_rest_context(workout)
        return render(request, "workouts/live.html", context)


class LiveExerciseView(LoginRequiredMixin, View):
    """Модалка «+ Упражнение»: поиск по видимым упражнениям и быстрое создание."""

    def get(self, request, pk):
        workout = live_workout_or_404(request, pk)
        context = self.search_context(request, workout)
        if "q" in request.GET:
            return render(request, "workouts/_exercise_results.html", context)
        return render(request, "workouts/_exercise_modal.html", context)

    def post(self, request, pk):
        workout = live_workout_or_404(request, pk)
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
            try:
                with transaction.atomic():
                    services.create_planned_sets(workout, exercise)
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
        # Выбранные единица и группа мышц возвращаются на круг: чипы живут в
        # свапаемом блоке результатов, и без этого следующая набранная буква
        # сбросила бы выбор.
        chosen = request.GET.get("measurement") or request.POST.get("measurement") or ""
        group = (
            request.GET.get("muscle_group_own")
            or request.GET.get("muscle_group")
            or request.POST.get("muscle_group_own")
            or request.POST.get("muscle_group")
            or ""
        )
        return {
            "workout": workout,
            "exercises": list(exercises[:EXERCISE_RESULTS_LIMIT]),
            "q": query,
            "offer_create": offer_create,
            "form": form,
            "measurement_choices": Exercise.Measurement.choices,
            "selected_measurement": (
                chosen
                if chosen in Exercise.Measurement.values
                else Exercise.Measurement.WEIGHT_REPS
            ),
            # Только при предложении создать: на поиске список групп не нужен,
            # и лишний запрос на каждую набранную букву тоже.
            "muscle_groups": muscle_groups_for(request.user) if offer_create else [],
            "selected_muscle_group": group.strip(),
            "muscle_group_max_length": MUSCLE_GROUP_MAX_LENGTH,
        }


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
        workout = live_workout_or_404(request, pk)
        exercise_id = request.POST.get("exercise", "")
        if not exercise_id.isdecimal():
            raise Http404("Упражнение не найдено")
        exercise = get_object_or_404(
            Exercise.objects.filter(sets__workout=workout).distinct(), pk=int(exercise_id)
        )
        last = workout.sets.filter(exercise=exercise).order_by("-set_number").first()
        try:
            with transaction.atomic():
                StrengthSet.objects.create(
                    workout=workout,
                    exercise=exercise,
                    set_number=last.set_number + 1 if last else 1,
                    measurement=exercise.measurement,
                    **services.set_values(exercise.measurement, last),
                )
        except IntegrityError:
            pass  # даблтап — второй подход не нужен
        return live_region_response(request, workout)


class ExerciseNoteView(LoginRequiredMixin, View):
    """Заметка к упражнению тренировки: модалка на GET, сохранение на POST.

    Упражнение резолвится ЧЕРЕЗ тренировку, а тренировка — через
    live_workout_or_404, поэтому чужая тренировка, завершённая (заметка после
    записи только читается) и упражнение не из этой тренировки дают 404 без
    отдельных проверок.

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
        workout = live_workout_or_404(request, pk)
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


class SetAdjustView(LoginRequiredMixin, View):
    """Степперы веса, повторов и времени: каждый тап сохраняется сразу."""

    def post(self, request, pk):
        field = request.POST.get("field", "")
        direction = request.POST.get("dir", "")
        if field not in SET_STEPS or direction not in {"up", "down"}:
            return HttpResponseBadRequest("Недопустимый шаг")
        row = live_set_or_404(request, pk, undone_only=True, for_update=True)
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
        setattr(row, field, value)
        row.save(update_fields=[field])
        return HttpResponse(row.field_display(field))


class SetValueView(LoginRequiredMixin, View):
    """Ввод значения подхода руками: тап по числу вместо серии тапов по «+».

    Абсолютное значение, а не шаг: человек написал ровно то, что хотел, и
    сорока тапов по «+» для сотни килограммов больше не нужно.
    """

    def post(self, request, pk):
        field = request.POST.get("field", "")
        if field not in SET_STEPS:
            return HttpResponseBadRequest("Недопустимое поле")
        row = live_set_or_404(request, pk, undone_only=True, for_update=True)
        if field not in MEASUREMENT_FIELDS[row.measurement]:
            return HttpResponseBadRequest("Поле не подходит единице упражнения")
        try:
            value = parse_field_value(field, request.POST.get("value", ""))
        except ValueError as error:
            # 400 с человеческим текстом: его показывает клиент рядом со полем.
            return HttpResponseBadRequest(str(error))
        setattr(row, field, value)
        row.save(update_fields=[field])
        return HttpResponse(row.field_display(field))


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
        return live_region_response(request, row.workout, restart_timer=True)


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
    """«Убрать подход»: удаляет невыполненный; выполненные неприкосновенны."""

    def post(self, request, pk):
        row = live_set_or_404(request, pk, undone_only=True)
        workout = row.workout
        row.delete()
        # Номера не пересчитываются: на экране подходы нумеруются по позиции,
        # а перенумерация рисковала бы упереться в уникальный индекс.
        # Если это был последний подход упражнения, его заметке в тренировке
        # больше не место — иначе она всплыла бы при повторном добавлении.
        services.drop_orphan_notes(workout)
        return live_region_response(request, workout)


class WorkoutLocationView(LoginRequiredMixin, View):
    """Место тренировки: модалка на GET, сохранение на POST.

    Тренировка берётся любая своя, в любом состоянии. Не live_workout_or_404:
    экрана правки силовой в проекте нет, и без этого забытое место записанной
    тренировки осталось бы неисправимым навсегда — а сравнение по залам
    строилось бы на вранье. Чужая по прямому URL даёт 404 фильтром по user.
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


class WorkoutFinishView(LoginRequiredMixin, View):
    """Завершение: длительность от started_at, плановые подходы удаляются."""

    def get_workout(self):
        workout = get_object_or_404(
            Workout.objects.filter(user=self.request.user).select_related("sport"),
            pk=self.kwargs["pk"],
        )
        if not workout.sport.is_strength:
            raise Http404("Завершение есть только у силовых тренировок")
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
        return render(
            request,
            "workouts/_finish_modal.html",
            {"workout": workout, "done_count": workout.sets.filter(done=True).count()},
        )

    def post(self, request, pk):
        workout = self.get_workout()
        if workout.is_finished:
            # Даблтап или кнопка «назад»: тренировка уже завершена.
            return redirect("workout_summary", pk=workout.pk)
        workout.sets.filter(done=False).delete()
        # Упражнение, которое так и не сделали, уходит вместе с плановыми
        # подходами — и его заметка тоже.
        services.drop_orphan_notes(workout)
        if not workout.sets.exists():
            workout.delete()
            messages.info(request, "Тренировка не записана: нет выполненных подходов.")
            return redirect("workout_history")
        workout.duration_min = max(1, min(MAX_DURATION_HOURS * 60, workout.elapsed_min))
        workout.save(update_fields=["duration_min"])
        messages.success(request, "Тренировка записана.")
        return redirect("workout_summary", pk=workout.pk)


class WorkoutSummaryView(LoginRequiredMixin, View):
    """Итог силовой тренировки: упражнения с подходами и метрика нагрузки."""

    def get(self, request, pk):
        workout = get_object_or_404(
            # Аннотации нагрузки — тем же запросом: их читает workout.workload,
            # а метрика итога должна совпадать с карточкой в ленте.
            Workout.objects.filter(user=request.user)
            .annotate(**stats.WORKLOAD_ANNOTATIONS)
            .select_related("sport", "location"),
            pk=pk,
        )
        if not workout.sport.is_strength:
            raise Http404("У кардио свой экран правки")
        if not workout.is_finished:
            return redirect("workout_live", pk=workout.pk)
        groups = services.exercise_groups(workout)
        for group in groups:
            group["total"] = services.exercise_total(group["sets"])
        return render(
            request,
            "workouts/workout_summary.html",
            {
                "workout": workout,
                "groups": groups,
                "nav_active": "history",
            },
        )


class WorkoutRepeatView(LoginRequiredMixin, View):
    """«Повторить»: новая активная тренировка с тем же набором упражнений.

    Веса подставляются не из источника, а из последней тренировки с каждым
    упражнением — по общему правилу подстановки.
    """

    def post(self, request, pk):
        source = get_object_or_404(
            Workout.objects.filter(user=request.user, sport__category=Sport.Category.STRENGTH)
            .finished()
            .select_related("sport"),
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
            services.create_planned_sets(workout, group["exercise"])
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
    """Главная: сводка за 7 дней, часы по неделям, рекорды, последние тренировки."""

    template_name = "workouts/dashboard.html"
    extra_context = {"nav_active": "dashboard"}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        chart = stats.weekly_chart(user)
        # Рекорды считаются один раз: прожектору нужен тот же топ, что и плиткам.
        strength = stats.strength_records(user, limit=STRENGTH_RECORDS_LIMIT)
        context.update(
            {
                "summary": stats.seven_day_summary(user),
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
            .select_related("sport", "cardio")
            .annotate(**stats.WORKLOAD_ANNOTATIONS)
            .order_by("-started_at", "-id")
        )
        today = timezone.localdate()
        return render(
            request,
            "workouts/_dashboard_week.html",
            {
                "title": week_title(start, today),
                "rows": [stats.workout_row(workout, today) for workout in workouts],
            },
        )


class ExerciseDetailView(LoginRequiredMixin, View):
    """Страница упражнения: график метрики и история подходов.

    Страница глобального упражнения видна всем, но данные — только свои:
    прогресс фильтруется по request.user.
    """

    def get(self, request, pk):
        exercise = visible_exercise_with_step(request.user, pk)
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
            request,
            template,
            {
                "exercise": exercise,
                "history": list(reversed(progress)),
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
                "can_edit_measurement": exercise.owner_id == request.user.pk,
                **weight_step_context(exercise),
                "muscle_groups": muscle_groups_for(request.user),
                "max_length": MUSCLE_GROUP_MAX_LENGTH,
                "stats_line": stats_line,
                # Разрез по местам считается в Python по уже загруженным
                # группам — ни одного нового запроса, бюджет страницы цел.
                "by_location": stats.progress_by_location(exercise, progress),
                "in_panel": in_panel,
                "nav_active": "exercises",
            },
        )


class ExerciseMeasurementView(LoginRequiredMixin, View):
    """Смена единицы своего упражнения. Записанные подходы не меняются: у них
    свой снимок единицы, и история остаётся в том виде, в котором её записали."""

    def post(self, request, pk):
        # Глобальное упражнение правит только админ, чужое личное — никто.
        exercise = get_object_or_404(Exercise.objects.filter(owner=request.user), pk=pk)
        measurement = request.POST.get("measurement", "")
        if measurement not in Exercise.Measurement.values:
            return HttpResponseBadRequest("Неизвестная единица")
        exercise.measurement = measurement
        exercise.save(update_fields=["measurement"])
        return render(
            request,
            "workouts/_measurement_choice.html",
            {"exercise": exercise, "can_edit_measurement": True, "saved": True},
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


def visible_exercise_with_step(user, pk):
    """Видимое упражнение вместе с шагом веса этого пользователя — одним запросом."""
    queryset = with_weight_step(Exercise.objects.visible_to(user), user.pk, exercise_ref="pk")
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
        # Глобальное упражнение правит только админ, чужое личное — никто.
        exercise = get_object_or_404(Exercise.objects.filter(owner=request.user), pk=pk)
        exercise.muscle_group = chosen_muscle_group(request.POST, muscle_groups_for(request.user))
        exercise.save(update_fields=["muscle_group"])
        return render(
            request,
            "workouts/_muscle_group_choice.html",
            {
                "exercise": exercise,
                "muscle_groups": muscle_groups_for(request.user),
                "max_length": MUSCLE_GROUP_MAX_LENGTH,
                "can_edit_measurement": True,
                "saved": True,
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
        # Счётчик использований нужен и подписи строки, и делению на «я тренирую»
        # / «остальное». Подпись говорит «в N тренировках», поэтому считаем
        # записанные: плановые подходы черновика тренировками ещё не стали.
        # Условие одно на оба агрегата: две скопированные руками копии со временем
        # разъедутся, а дата обязана совпадать с тем, что посчитал счётчик.
        mine = Q(
            sets__workout__user=self.request.user,
            sets__workout__duration_min__isnull=False,
        )
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
        # Справочник ниже остаётся полным: выполненное упражнение показывается и
        # плиткой, и строкой в своей группе — иначе в «Груди» не оказалось бы жима
        # лёжа, и искать его глазами по группе стало бы бесполезно.
        groups = group_by_muscle(context["exercises"])
        context["query"] = self.request.GET.get("q", "").strip()
        context["mine_only"] = bool(self.request.GET.get("mine"))
        context["groups"] = groups
        context["muscle_groups"] = self.known_groups()
        context["group_filter"] = self.chosen_group()
        filtered = bool(context["query"] or context["mine_only"] or context["group_filter"])
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
        return context

    def known_groups(self):
        """Группы мышц из данных. Кешируем: их спрашивают и фильтр, и чипы."""
        if not hasattr(self, "_known_groups"):
            self._known_groups = muscle_groups_for(self.request.user)
        return self._known_groups

    def chosen_group(self):
        """Выбранная группа мышц — ровно в том написании, что лежит в данных.

        Сравнение регистронезависимое, а неизвестная группа просто игнорируется:
        чипы приходят из данных, и в открытой вкладке они могли устареть.
        """
        wanted = self.request.GET.get("group", "").strip().lower()
        if not wanted:
            return ""
        return next((group for group in self.known_groups() if group.lower() == wanted), "")


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
                )
            )
            .order_by("name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for sport in context["sports"]:
            sport.usage_label = usage_label(sport.workouts_count)
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

    def get_object(self):
        # Чужая и глобальная запись по прямому URL — 404.
        return get_object_or_404(
            self.model.objects.filter(owner=self.request.user), pk=self.kwargs["pk"]
        )

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


class ChangelogView(LoginRequiredMixin, View):
    """«Что нового». Открытие страницы отмечает новости прочитанными.

    GET меняет состояние осознанно: это обычный «прочитано при открытии» —
    пишется одна колонка своей же строки, повторные открытия просто сдвигают
    отметку вперёд, а при ошибке рендера транзакция откатится и новости
    останутся непрочитанными.
    """

    def get(self, request):
        entries = list(ChangelogEntry.objects.published())
        today = timezone.localdate()
        for entry in entries:
            moment = timezone.localtime(entry.published_at)
            entry.date_label = formats.date_format(
                moment, "j E" if moment.year == today.year else "j E Y"
            )
        request.user.changelog_seen_at = timezone.now()
        request.user.save(update_fields=["changelog_seen_at"])
        return render(
            request,
            "workouts/changelog.html",
            {"entries": entries, "nav_active": "profile"},
        )
