"""Экран профиля: аккаунт, тема, отдых по умолчанию, входы в справочники."""

from allauth.account.utils import has_verified_email
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.generic import TemplateView, View

from accounts.forms import WeeklyGoalForm
from accounts.models import User
from workouts import stats
from workouts.models import (
    REST_DELTAS,
    BodyMeasurement,
    ChangelogEntry,
    Exercise,
    Location,
    Sport,
    clamp_rest_seconds,
    rest_display,
)


def owned_counts(user):
    """Число своих упражнений, видов спорта и замеров — одним запросом.

    Подзапросами к строке пользователя, а не тремя .count(): бюджет профиля
    упирался в потолок, и третий счётчик (замеры) пробил бы его. Каждый
    подзапрос — GROUP BY по своей связи, поэтому строки не размножаются.
    """

    def count(model, field):
        rows = (
            model.objects.filter(**{field: OuterRef("pk")})
            .order_by()
            .values(field)
            .annotate(total=Count("pk"))
            .values("total")
        )
        return Coalesce(Subquery(rows), 0)

    return (
        User.objects.filter(pk=user.pk)
        .values(
            exercises_count=count(Exercise, "owner"),
            sports_count=count(Sport, "owner"),
            measurements_count=count(BodyMeasurement, "user"),
        )
        .get()
    )


def rest_context(user, *, oob=False):
    """Контекст степпера и строки профиля — как live_rest_context в workouts."""
    seconds = user.rest_seconds_default
    return {"rest_seconds": seconds, "rest_display": rest_display(seconds), "oob": oob}


class ProfileView(LoginRequiredMixin, TemplateView):
    """Профиль: почта, тема, настройка отдыха, свои справочники, новости, аккаунт."""

    template_name = "accounts/profile.html"

    def get_context_data(self, **kwargs):
        user = self.request.user
        context = super().get_context_data(**kwargs) | rest_context(user)
        # Счётчик мест и название дефолта — одним агрегатом: бюджет профиля
        # тесный, а дефолт ровно один (частичный уникальный индекс), поэтому
        # Max по имени с фильтром и есть его название.
        locations = Location.objects.filter(owner=user).aggregate(
            total=Count("pk"),
            default_name=Max("name", filter=Q(is_default=True)),
        )
        context.update(
            {
                "nav_active": "profile",
                "locations_count": locations["total"],
                "default_location": locations["default_name"],
                "app_version": settings.APP_VERSION,
                # Ветка «не подтверждён» почти недостижима (проверка почты
                # обязательна), но админ может создать пользователя без адреса.
                "email_verified": has_verified_email(user),
                **owned_counts(user),
                "changelog_unread": ChangelogEntry.objects.unread_for(user).exists(),
            }
        )
        return context


class ProfileRestView(LoginRequiredMixin, View):
    """Модалка «Отдых между подходами»: ±15 сек, каждый тап сохраняется сразу."""

    def get(self, request):
        return render(request, "accounts/_rest_modal.html", rest_context(request.user))

    def post(self, request):
        delta = request.POST.get("delta", "")
        if delta not in REST_DELTAS:
            return HttpResponseBadRequest("Недопустимый шаг")
        user = request.user
        user.rest_seconds_default = clamp_rest_seconds(user.rest_seconds_default + int(delta))
        user.save(update_fields=["rest_seconds_default"])
        # Обе видимые копии значения (в модалке и в строке профиля) обновляются
        # out-of-band: свапать по месту нечего, кнопки идут с hx-swap="none".
        context = rest_context(user, oob=True)
        html = render_to_string("accounts/_rest_value.html", context, request=request)
        html += render_to_string("accounts/_rest_row_value.html", context, request=request)
        return HttpResponse(html)


class ProfileGoalView(LoginRequiredMixin, View):
    """Модалка «Цель на неделю»: часы в неделю или «убрать цель».

    Открывается из карточки цели на дашборде. Ответ на сохранение — карточка
    out-of-band и пустая модалка, которая поэтому закрывается сама: та же схема,
    что у дня черновика. Ошибка ввода — модалка с подсказкой. Цель — поле самого
    пользователя, поэтому чужой цели по адресу не достать: адрес без id.
    """

    template_name = "accounts/_goal_modal.html"

    def get(self, request):
        minutes = request.user.weekly_goal_minutes
        initial = {"hours": stats.hours_display(minutes)} if minutes else {}
        return self.modal(request, WeeklyGoalForm(initial=initial))

    def post(self, request):
        user = request.user
        if request.POST.get("clear"):
            user.weekly_goal_minutes = None
        else:
            form = WeeklyGoalForm(request.POST)
            if not form.is_valid():
                return self.modal(request, form)
            user.weekly_goal_minutes = form.cleaned_data["hours"]
        user.save(update_fields=["weekly_goal_minutes"])
        # Минуты недели — тем же счётом, что у графика на дашборде: иначе
        # карточка после сохранения могла бы разойтись с соседним столбцом.
        chart = stats.weekly_chart(user)
        goal = stats.week_goal(user.weekly_goal_minutes, chart["totals"], timezone.localdate())
        return render(request, "workouts/_week_goal.html", {"goal": goal, "oob": True})

    def modal(self, request, form):
        has_goal = request.user.weekly_goal_minutes is not None
        return render(request, self.template_name, {"form": form, "has_goal": has_goal})
