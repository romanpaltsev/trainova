from django.conf import settings
from django.utils.functional import SimpleLazyObject

from workouts.models import ChangelogEntry


def honeypot(request):
    """Имя honeypot-поля allauth — чтобы шаблон формы знал, какое поле скрыть."""
    return {"honeypot_field": settings.ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD}


def theme_colors(request):
    """Цвета тем для <meta name="theme-color"> и манифеста — из settings, не из шаблона."""
    return {"theme_colors": settings.APP_THEME_COLORS}


def changelog_unread(request):
    """Есть ли непрочитанные новости — точка у «Что нового» в боковой панели ПК.

    Значение ленивое: запрос (один EXISTS) уходит, только когда шаблон его
    читает, — на полных страницах, где рисуется панель. htmx-фрагменты рендерятся
    с теми же процессорами, но панели в них нет, и запроса они не делают.
    Профиль считает то же значение сам, а «Что нового» отдаёт False (её только что
    открыли): значение вьюхи перекрывает процессор, и второго запроса нет.
    """

    def unread():
        user = request.user
        return user.is_authenticated and ChangelogEntry.objects.unread_for(user).exists()

    return {"changelog_unread": SimpleLazyObject(unread)}
