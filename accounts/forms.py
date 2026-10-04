"""Формы allauth, приведённые к нашему оформлению.

allauth рендерит поля своими виджетами, поэтому Bootstrap-классы, русские подписи
и placeholder'ы задаём здесь — один раз для всех auth-форм.
"""

import re
from decimal import Decimal, InvalidOperation

from allauth.account import app_settings as allauth_settings
from allauth.account import forms as allauth_forms
from allauth.account.adapter import get_adapter
from allauth.account.utils import filter_users_by_email
from django import forms

from accounts import deletion
from accounts.models import WEEKLY_GOAL_MAX_MINUTES, WEEKLY_GOAL_MIN_MINUTES

LABELS = {
    "email": "Email",
    "email2": "Email (ещё раз)",
    "login": "Email",
    "password": "Пароль",
    "password1": "Пароль",
    "password2": "Пароль (ещё раз)",
    "oldpassword": "Текущий пароль",
}


class StyledFormMixin:
    """Bootstrap-классы на виджеты, русские подписи, без дублирующих placeholder'ов."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.pop("placeholder", None)
            css = "form-check-input" if field.widget.input_type == "checkbox" else "form-control"
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {css}".strip()
            if name in LABELS:
                field.label = LABELS[name]


class LoginForm(StyledFormMixin, allauth_forms.LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ссылку «Забыли пароль?» рисуем в шаблоне под формой, подсказка allauth не нужна.
        self.fields["password"].help_text = ""


class SignupForm(StyledFormMixin, allauth_forms.SignupForm):
    pass


class ResetPasswordForm(StyledFormMixin, allauth_forms.ResetPasswordForm):
    def clean_email(self):
        """Как у allauth, но аккаунт в отсрочке удаления тоже получает письмо.

        allauth ищет только активных, а ждущий удаления отключён — и человек,
        забывший пароль, получил бы «такого аккаунта нет», хотя его данные ещё
        30 дней живы. После сброса он входит, и вход возвращает аккаунт.
        """
        email = get_adapter().clean_email(self.cleaned_data["email"].lower())
        self.users = [
            user
            for user in filter_users_by_email(email, prefer_verified=True)
            if user.is_active or deletion.is_restorable(user)
        ]
        if not self.users and not allauth_settings.PREVENT_ENUMERATION:
            raise get_adapter().validation_error("unknown_email")
        return self.cleaned_data["email"]


class ResetPasswordKeyForm(StyledFormMixin, allauth_forms.ResetPasswordKeyForm):
    pass


class ChangePasswordForm(StyledFormMixin, allauth_forms.ChangePasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].label = "Новый пароль"
        self.fields["password2"].label = "Новый пароль (ещё раз)"
        self.fields["oldpassword"].help_text = ""


class SetPasswordForm(StyledFormMixin, allauth_forms.SetPasswordForm):
    pass


class DeleteAccountForm(StyledFormMixin, allauth_forms.ReauthenticateForm):
    """Подтверждение удаления аккаунта паролем.

    Проверку делает allauth (adapter.reauthenticate → authenticate): у неё уже
    есть ограничение неудачных попыток входа и русская ошибка, а своя проверка
    check_password дала бы перебирать пароль из украденной сессии без лимита.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].label = "Пароль"
        # Ссылку «Забыли пароль?» allauth кладёт в help_text — здесь она лишняя.
        self.fields["password"].help_text = ""


class WeeklyGoalForm(forms.Form):
    """Цель на неделю в часах: «4», «4,5» или «4:30» — как человеку удобнее.

    Хранится в минутах (User.weekly_goal_minutes): в них же считаются
    длительности тренировок, и карточка цели делит одно на другое без
    преобразований.
    """

    hours = forms.CharField(label="Часов в неделю", max_length=8)

    def clean_hours(self):
        raw = self.cleaned_data["hours"].strip()
        match = re.fullmatch(r"(\d{1,2}):([0-5]\d)", raw)
        if match:
            minutes = int(match[1]) * 60 + int(match[2])
        else:
            try:
                hours = Decimal(raw.replace(",", "."))
            except InvalidOperation:
                hours = None
            # NaN и Infinity Decimal принимает, а round() на них падает.
            if hours is None or not hours.is_finite():
                raise forms.ValidationError("Введите часы: 4, 4,5 или 4:30.")
            minutes = round(hours * 60)
        if not WEEKLY_GOAL_MIN_MINUTES <= minutes <= WEEKLY_GOAL_MAX_MINUTES:
            raise forms.ValidationError("Цель — от 0:30 до 40:00 в неделю.")
        return minutes
