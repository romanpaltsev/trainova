"""Экраны тренировок: лента истории, ввод кардио, живой режим, личные справочники.

Каждый queryset пользовательских данных фильтруется по request.user — чужая запись
по прямому URL даёт 404.
"""

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Sum
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import DeleteView, ListView, View

from workouts import services
from workouts.forms import MAX_DURATION_HOURS, CardioWorkoutForm, ExerciseQuickForm, SportForm
from workouts.models import Exercise, Sport, StrengthSet, Workout
from workouts.stats import week_start, week_title

HISTORY_PAGE_SIZE = 10

# Живой режим: шаги степперов и границы значений.
SET_STEPS = {"weight": Decimal("2.5"), "reps": 1}
MAX_WEIGHT_KG = Decimal("999.99")  # max_digits=5 у StrengthSet.weight_kg
MAX_REPS = 999
REST_DELTAS = {"-15", "15"}
REST_MIN_SECONDS = 15
REST_MAX_SECONDS = 600
EXERCISE_RESULTS_LIMIT = 30


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
            Sport.objects.filter(workouts__user=self.request.user).distinct().order_by("name")
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
        return self.render_form(CardioWorkoutForm(user=request.user, instance=instance), instance)

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
    """Удаление своей тренировки с подтверждением."""

    template_name = "workouts/workout_confirm_delete.html"
    context_object_name = "workout"
    success_url = reverse_lazy("workout_history")

    def get_queryset(self):
        return Workout.objects.filter(user=self.request.user).select_related("sport")

    def form_valid(self, form):
        messages.success(self.request, "Тренировка удалена.")
        return super().form_valid(form)


# ---------- Живой режим силовой тренировки ----------


def live_workout_or_404(request, pk):
    """Своя активная силовая тренировка — база всех действий живого режима."""
    return get_object_or_404(
        Workout.objects.filter(user=request.user, sport__category=Sport.Category.STRENGTH)
        .in_progress()
        .select_related("sport"),
        pk=pk,
    )


def live_set_or_404(request, pk, *, undone_only=False, for_update=False):
    """Свой подход своей активной тренировки; подходы завершённых неизменяемы."""
    queryset = StrengthSet.objects.filter(
        workout__user=request.user, workout__duration_min__isnull=True
    ).select_related("workout", "exercise")
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
        "rest_display": f"{seconds // 60}:{seconds % 60:02d}",
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
    """HTMX-модалка «+»: продолжить активную, начать силовую или записать кардио."""

    def get(self, request):
        return render(
            request,
            "workouts/_start_modal.html",
            {
                "active": Workout.objects.filter(user=request.user).in_progress().first(),
                "strength_sports": Sport.objects.visible_to(request.user).filter(
                    category=Sport.Category.STRENGTH
                ),
            },
        )


class StrengthWorkoutStartView(LoginRequiredMixin, View):
    """Старт живого режима: активная тренировка создаётся сразу, форма не нужна."""

    def post(self, request):
        sport_id = request.POST.get("sport", "")
        if not sport_id.isdecimal():
            raise Http404("Вид спорта не указан")
        sport = get_object_or_404(
            Sport.objects.visible_to(request.user).filter(category=Sport.Category.STRENGTH),
            pk=int(sport_id),
        )
        try:
            # Вложенный atomic: гонку двух вкладок ловит частичный уникальный индекс,
            # а savepoint не даёт IntegrityError отравить транзакцию запроса.
            with transaction.atomic():
                workout = Workout.objects.create(
                    user=request.user, sport=sport, started_at=timezone.now(), duration_min=None
                )
        except IntegrityError:
            workout = Workout.objects.filter(user=request.user).in_progress().first()
            if workout is None:
                raise
        return redirect("workout_live", pk=workout.pk)


class LiveWorkoutView(LoginRequiredMixin, View):
    """Экран живого режима силовой тренировки."""

    def get(self, request, pk):
        workout = get_object_or_404(
            Workout.objects.filter(user=request.user).select_related("sport"), pk=pk
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
        seconds = workout.effective_rest_seconds + int(delta)
        workout.rest_seconds = max(REST_MIN_SECONDS, min(REST_MAX_SECONDS, seconds))
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
        row = live_set_or_404(request, pk, for_update=True)
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
        active = Workout.objects.filter(user=request.user).in_progress().first()
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
            active = Workout.objects.filter(user=request.user).in_progress().first()
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
