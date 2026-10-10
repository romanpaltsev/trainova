from django.conf import settings
from django.db.models import Count, Exists, IntegerField, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils.functional import SimpleLazyObject

from accounts.models import User
from feedback.models import Feedback, FeedbackMessage
from workouts.models import CatalogRequest, ChangelogEntry


def honeypot(request):
    """Имя honeypot-поля allauth — чтобы шаблон формы знал, какое поле скрыть."""
    return {"honeypot_field": settings.ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD}


def theme_colors(request):
    """Цвета тем для <meta name="theme-color"> и манифеста — из settings, не из шаблона."""
    return {"theme_colors": settings.APP_THEME_COLORS}


def badge_counts(user):
    """Бейджи панели одним запросом к строке пользователя.

    badge_changelog — есть непрочитанные новости, badge_feedback — есть
    непрочитанный ответ на обращение, badge_admin_new — сколько новых обращений
    и заявок в общий справочник ждут администратора (только у админа:
    остальным подзапросы даже не строятся).
    """
    # Имена с префиксом badge_: «feedback» уже занято обратной связью User.
    annotations = {
        "badge_changelog": Exists(ChangelogEntry.objects.unread_for(user)),
        "badge_feedback": Exists(FeedbackMessage.objects.unread().filter(feedback__user=user)),
    }
    if user.is_admin:
        # Одно число на весь проект — скалярным подзапросом; GROUP BY по
        # статусу даёт ровно одну строку или ни одной (тогда 0).
        new = (
            Feedback.objects.filter(status=Feedback.Status.NEW)
            .order_by()
            .values("status")
            .annotate(n=Count("pk"))
            .values("n")
        )
        pending = (
            CatalogRequest.objects.filter(status=CatalogRequest.Status.PENDING)
            .order_by()
            .values("status")
            .annotate(n=Count("pk"))
            .values("n")
        )
        annotations["badge_admin_new"] = Coalesce(
            Subquery(new, output_field=IntegerField()), Value(0)
        ) + Coalesce(Subquery(pending, output_field=IntegerField()), Value(0))
    return User.objects.filter(pk=user.pk).values(**annotations).get()


def sidebar_badges(request):
    """Точки и счётчики боковой панели и профиля: «Что нового», «Обратная связь», «Админка».

    Значения ленивые и общие: первый, кто их прочитает, делает один запрос
    (badge_counts), остальные берут готовое. Запрос уходит, только когда шаблон
    их читает, — на полных страницах, где рисуется панель или профиль.
    htmx-фрагменты рендерятся с теми же процессорами, но панели в них нет, и
    запроса они не делают.
    """
    cache = {}

    def counts():
        if "value" not in cache:
            user = request.user
            cache["value"] = badge_counts(user) if user.is_authenticated else {}
        return cache["value"]

    def lazy(key, default):
        return SimpleLazyObject(lambda: counts().get(f"badge_{key}", default))

    return {
        "changelog_unread": lazy("changelog", False),
        "feedback_unread": lazy("feedback", False),
        "admin_new_feedback": lazy("admin_new", 0),
    }
