"""Экраны тренировок: лента истории, ввод кардио, живой режим, личные справочники.

Каждый queryset пользовательских данных фильтруется по request.user — чужая запись
по прямому URL даёт 404.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, Sum
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
    REST_DELTAS,
    ChangelogEntry,
    Exercise,
    Sport,
    StrengthSet,
    Workout,
    clamp_rest_seconds,
    decimal_display,
    rest_display,
)
from workouts.stats import week_start, week_title

HISTORY_PAGE_SIZE = 10

# Живой режим: шаги степперов и границы значений.
SET_STEPS = {"weight": Decimal("2.5"), "reps": 1}
MAX_WEIGHT_KG = Decimal("999.99")  # max_digits=5 у StrengthSet.weight_kg
MAX_REPS = 999
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
            # cardio — обратная OneToOne, тянется тем же запросом
            .select_related("sport", "cardio")
            # Иначе каждая силовая карточка делала бы свой COUNT по подходам
            .annotate(
                exercises_count=Count("sets__exercise", distinct=True),
                tonnage=Sum(F("sets__weight_kg") * F("sets__reps")),
            )
            # Сортировку задаём явно: в запросах с GROUP BY Django игнорирует
            # Meta.ordering, а пагинации нужен детерминированный порядок.
            .order_by("-started_at", "-id")
        )
        sport_id = self.request.GET.get("sport")
        if sport_id and sport_id.isdecimal():
            queryset = queryset.filter(sport_id=int(sport_id))
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
    word = services.ru_plural(count, "упражнение", "упражнения", "упражнений")
    return f"{count} {word}"


def live_workout_or_404(request, pk):
    """Своя незавершённая силовая — база всех действий живого режима.

    Черновик правится тем же набором эндпоинтов, что и идущая тренировка:
    подготовка — это и есть добавление упражнений и правка весов.
    """
    return get_object_or_404(
        Workout.objects.filter(user=request.user, sport__category=Sport.Category.STRENGTH)
        .unfinished()
        # user — для отдыха по умолчанию и подсказок, иначе он тянется отдельным запросом
        .select_related("sport", "user"),
        pk=pk,
    )


def live_set_or_404(request, pk, *, undone_only=False, for_update=False, started_only=False):
    """Свой подход своей незавершённой тренировки; подходы завершённых неизменяемы."""
    queryset = StrengthSet.objects.filter(
        workout__user=request.user, workout__duration_min__isnull=True
    ).select_related("workout", "exercise")
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
        if self.planned:
            # Черновиков может быть сколько угодно: уникальный индекс требует начала,
            # поэтому ловить IntegrityError здесь не нужно.
            workout = Workout.objects.create(
                user=request.user, sport=sport, started_at=None, duration_min=None
            )
            return redirect("workout_live", pk=workout.pk)
        try:
            # Вложенный atomic: гонку двух вкладок ловит частичный уникальный индекс,
            # а savepoint не даёт IntegrityError отравить транзакцию запроса.
            with transaction.atomic():
                workout = Workout.objects.create(
                    user=request.user, sport=sport, started_at=timezone.now(), duration_min=None
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
            Workout.objects.filter(user=request.user).select_related("sport", "user"), pk=pk
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
        return {
            "workout": workout,
            "exercises": list(exercises[:EXERCISE_RESULTS_LIMIT]),
            "q": query,
            "offer_create": offer_create,
            "form": form,
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
                    weight_kg=last.weight_kg if last else 0,
                    reps=last.reps if last else 0,
                )
        except IntegrityError:
            pass  # даблтап — второй подход не нужен
        return live_region_response(request, workout)


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
    """Степперы веса и повторов: каждый тап сохраняется сразу."""

    def post(self, request, pk):
        field = request.POST.get("field", "")
        direction = request.POST.get("dir", "")
        if field not in SET_STEPS or direction not in {"up", "down"}:
            return HttpResponseBadRequest("Недопустимый шаг")
        row = live_set_or_404(request, pk, undone_only=True, for_update=True)
        step = SET_STEPS[field] if direction == "up" else -SET_STEPS[field]
        if field == "weight":
            row.weight_kg = min(MAX_WEIGHT_KG, max(Decimal(0), Decimal(str(row.weight_kg)) + step))
            row.save(update_fields=["weight_kg"])
            return HttpResponse(row.weight_display)
        row.reps = min(MAX_REPS, max(0, row.reps + step))
        row.save(update_fields=["reps"])
        return HttpResponse(str(row.reps))


class SetDoneView(LoginRequiredMixin, View):
    """«Подход выполнен»: фиксирует подход и перезапускает таймер отдыха."""

    def post(self, request, pk):
        # started_only: в черновике этой кнопки нет, но устаревшая вкладка есть всегда.
        row = live_set_or_404(request, pk, for_update=True, started_only=True)
        if row.done:
            # Даблтап: подход уже записан, отдых перезапускать нельзя.
            return live_region_response(request, row.workout)
        if row.reps < 1:
            return live_region_response(request, row.workout, error="Укажите повторения.")
        row.done = True
        row.save(update_fields=["done"])
        return live_region_response(request, row.workout, restart_timer=True)


class SetUndoView(LoginRequiredMixin, View):
    """Тап по выполненному подходу — вернуть его в работу, значения сохраняются."""

    def post(self, request, pk):
        row = live_set_or_404(request, pk, for_update=True)
        if row.done:
            row.done = False
            row.save(update_fields=["done"])
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
        return live_region_response(request, workout)


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
        if not workout.sets.exists():
            workout.delete()
            messages.info(request, "Тренировка не записана: нет выполненных подходов.")
            return redirect("workout_history")
        workout.duration_min = max(1, min(MAX_DURATION_HOURS * 60, workout.elapsed_min))
        workout.save(update_fields=["duration_min"])
        messages.success(request, "Тренировка записана.")
        return redirect("workout_summary", pk=workout.pk)


class WorkoutSummaryView(LoginRequiredMixin, View):
    """Итог силовой тренировки: упражнения с подходами и тоннаж."""

    def get(self, request, pk):
        workout = get_object_or_404(
            Workout.objects.filter(user=request.user).select_related("sport"), pk=pk
        )
        if not workout.sport.is_strength:
            raise Http404("У кардио свой экран правки")
        if not workout.is_finished:
            return redirect("workout_live", pk=workout.pk)
        groups = services.exercise_groups(workout)
        for group in groups:
            group["tonnage"] = sum((s.tonnage_kg for s in group["sets"]), Decimal(0))
        return render(
            request,
            "workouts/workout_summary.html",
            {
                "workout": workout,
                "groups": groups,
                "total_tonnage": sum((g["tonnage"] for g in groups), Decimal(0)),
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
        try:
            with transaction.atomic():
                workout = Workout.objects.create(
                    user=request.user,
                    sport=source.sport,
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
                "value": f"{row['weight_display']} кг",
                "sub": "",
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
            .annotate(tonnage=Sum(F("sets__weight_kg") * F("sets__reps")))
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
    """Страница упражнения: график максимального веса и история подходов.

    Страница глобального упражнения видна всем, но данные — только свои:
    прогресс фильтруется по request.user.
    """

    def get(self, request, pk):
        exercise = get_object_or_404(Exercise.objects.visible_to(request.user), pk=pk)
        progress = stats.exercise_progress(request.user, exercise)
        count = len(progress)
        if count:
            record = max(group["max_weight"] for group in progress)
            workouts_word = services.ru_plural(count, "тренировка", "тренировки", "тренировок")
            stats_line = f"{count} {workouts_word}"
            if record:
                stats_line += f" · рекорд {decimal_display(Decimal(str(record)))} кг"
        else:
            stats_line = "ещё не было в тренировках"
        return render(
            request,
            "workouts/exercise_detail.html",
            {
                "exercise": exercise,
                "history": list(reversed(progress)),
                "chart": {
                    "labels": [group["label"] for group in progress],
                    "values": [group["max_weight"] for group in progress],
                    "colorKey": "strength",
                    "unit": "кг",
                },
                "stats_line": stats_line,
                "nav_active": "dashboard",
            },
        )


# ---------- Справочники в профиле и новости ----------


def usage_label(count):
    """Подпись строки справочника: «в 3 тренировках» или «не использовалось»."""
    if not count:
        return "не использовалось"
    word = services.ru_plural(count, "тренировке", "тренировках", "тренировках")
    return f"в {count} {word}"


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
        # Счётчик использований нужен только для своих записей — их и удаляем.
        # Подпись говорит «в N тренировках», поэтому считаем записанные: плановые
        # подходы черновика тренировками ещё не стали.
        return queryset.annotate(
            workouts_count=Count(
                "sets__workout",
                distinct=True,
                filter=Q(
                    sets__workout__user=self.request.user,
                    sets__workout__duration_min__isnull=False,
                ),
            )
        ).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        my_records = stats.strength_records(self.request.user)
        records = {row["exercise_id"]: row["weight_display"] for row in my_records}
        for exercise in context["exercises"]:
            exercise.record_display = records.get(exercise.pk)
            exercise.usage_label = usage_label(exercise.workouts_count)
        context["query"] = self.request.GET.get("q", "").strip()
        context["mine_only"] = bool(self.request.GET.get("mine"))
        return context


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
