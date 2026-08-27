from django.conf import settings


def honeypot(request):
    """Имя honeypot-поля allauth — чтобы шаблон формы знал, какое поле скрыть."""
    return {"honeypot_field": settings.ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD}
