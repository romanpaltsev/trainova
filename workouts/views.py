"""Экраны тренировок: лента истории, ввод кардио, личные виды спорта.

Каждый queryset пользовательских данных фильтруется по request.user — чужая запись
по прямому URL даёт 404.
"""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, F, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import DeleteView, ListView, View

from workouts.forms import CardioWorkoutForm, SportForm
from workouts.models import Sport, Workout

HISTORY_PAGE_SIZE = 10


def week_start(date):
    """Понедельник недели, к которой относится дата."""
    return date - timedelta(days=date.weekday())


class WorkoutHistoryView(LoginRequiredMixin, ListView):
    """Лента тренировок: карточками, по убыванию даты, с подгрузкой по кнопке."""

    template_name = "workouts/history.html"
    context_object_name = "workouts"
    paginate_by = HISTORY_PAGE_SIZE
    extra_context = {"nav_active": "history"}

    def get_queryset(self):
        queryset = (
            Workout.objects.filter(user=self.request.user)
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
        if sport_id and sport_id.isdigit():
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
                    {"key": start.isoformat(), "title": self._week_title(start, today), "items": []}
                )
            groups[-1]["items"].append(workout)
        return groups

    @staticmethod
    def _week_title(start, today):
        current = week_start(today)
        if start == current:
            return "Эта неделя"
        if start == current - timedelta(days=7):
            return "Прошлая неделя"
        end = start + timedelta(days=6)
        return f"{start:%d.%m} — {end:%d.%m}"


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
