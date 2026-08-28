"""Экран профиля: аккаунт, тема, отдых по умолчанию, входы в справочники."""

from allauth.account.utils import has_verified_email
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.generic import TemplateView, View

from workouts.models import (
    REST_DELTAS,
    ChangelogEntry,
    Exercise,
    Sport,
    clamp_rest_seconds,
    rest_display,
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
        context.update(
            {
                "nav_active": "profile",
                "app_version": settings.APP_VERSION,
                # Ветка «не подтверждён» почти недостижима (проверка почты
                # обязательна), но админ может создать пользователя без адреса.
                "email_verified": has_verified_email(user),
                "exercises_count": Exercise.objects.filter(owner=user).count(),
                "sports_count": Sport.objects.filter(owner=user).count(),
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
