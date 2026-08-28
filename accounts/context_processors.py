from django.conf import settings


def honeypot(request):
    """Имя honeypot-поля allauth — чтобы шаблон формы знал, какое поле скрыть."""
    return {"honeypot_field": settings.ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD}


def theme_colors(request):
    """Цвета тем для <meta name="theme-color"> и манифеста — из settings, не из шаблона."""
    return {"theme_colors": settings.APP_THEME_COLORS}
