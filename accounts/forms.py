"""Формы allauth, приведённые к нашему оформлению.

allauth рендерит поля своими виджетами, поэтому Bootstrap-классы, русские подписи
и placeholder'ы задаём здесь — один раз для всех auth-форм.
"""

import re
from decimal import Decimal, InvalidOperation

from allauth.account import forms as allauth_forms
from django import forms

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
    pass


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
