from allauth.account.adapter import DefaultAccountAdapter
from django.urls import reverse


class AccountAdapter(DefaultAccountAdapter):
    """Адаптер allauth под наши правила: письма от имени продукта, без Site-домена в темах."""

    def get_email_subject_prefix(self, context=None):
        return ""

    def get_password_change_redirect_url(self, request):
        """После смены пароля возвращаем на дашборд, а не на форму смены."""
        return reverse("dashboard")
