from allauth.account.adapter import DefaultAccountAdapter
from django.contrib import messages
from django.urls import reverse

from accounts import deletion


class AccountAdapter(DefaultAccountAdapter):
    """Адаптер allauth под наши правила: письма от имени продукта, без Site-домена в темах."""

    def get_email_subject_prefix(self, context=None):
        return ""

    def get_password_change_redirect_url(self, request):
        """После смены пароля возвращаем на дашборд, а не на форму смены."""
        return reverse("dashboard")

    def pre_login(self, request, user, **kwargs):
        """Вход в течение 30 дней после запроса удаления возвращает аккаунт.

        Сюда попадаем только с верным паролем: allauth «прячет» неактивного
        пользователя, у которого пароль совпал, и ведёт его по обычному входу.
        После срока — прежний ответ «аккаунт отключён»: удаляет только команда
        purge_deleted, и второй путь удаления (прямо во время входа) не нужен.
        """
        if user.deletion_requested_at is not None:
            if not deletion.is_restorable(user):
                return self.respond_user_inactive(request, user)
            deletion.cancel(user)
            messages.success(request, "С возвращением! Удаление аккаунта отменено.")
        return super().pre_login(request, user, **kwargs)
