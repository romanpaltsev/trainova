"""Формы allauth, приведённые к нашему оформлению.

allauth рендерит поля своими виджетами, поэтому Bootstrap-классы, русские подписи
и placeholder'ы задаём здесь — один раз для всех auth-форм.
"""

from allauth.account import forms as allauth_forms

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
