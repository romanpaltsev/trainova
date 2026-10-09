from datetime import timedelta

from django import forms
from django.utils import timezone

from feedback.models import (
    FEEDBACK_TEXT_MAX_LENGTH,
    MESSAGE_MAX_LENGTH,
    Feedback,
    FeedbackMessage,
)

# Мягкая защита от случайного спама (двойная отправка, залипшая кнопка), а не от
# злоумышленника: пишут сюда только вошедшие, друзья и родственники автора.
DAILY_LIMIT = 10
LIMIT_ERROR = "За сутки можно отправить не больше 10 сообщений — продолжим завтра."


def sent_today(user):
    """Сколько человек написал за сутки: и новые обращения, и дописки к старым."""
    since = timezone.now() - timedelta(days=1)
    return (
        Feedback.objects.filter(user=user, created_at__gte=since).count()
        + FeedbackMessage.objects.filter(
            feedback__user=user, from_admin=False, created_at__gte=since
        ).count()
    )


class FeedbackForm(forms.ModelForm):
    class Meta:
        model = Feedback
        fields = ("kind", "text")
        widgets = {
            "kind": forms.RadioSelect,
            "text": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
        }
        error_messages = {"text": {"required": "Напишите, что хотели сказать."}}

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["text"].widget.attrs["maxlength"] = FEEDBACK_TEXT_MAX_LENGTH

    def clean_text(self):
        return self.cleaned_data["text"].strip()

    def clean(self):
        cleaned = super().clean()
        if sent_today(self.user) >= DAILY_LIMIT:
            raise forms.ValidationError(LIMIT_ERROR)
        return cleaned

    def save(self, commit=True):
        self.instance.user = self.user
        return super().save(commit=commit)


class FeedbackMessageForm(forms.ModelForm):
    """Реплика в переписке. Чья она — решает вьюха (from_admin), а не поле формы.

    Для автора текст обязателен; у администратора форма идёт вместе со статусом,
    и пустой текст значит «только сменить статус» — поле там необязательное.
    """

    class Meta:
        model = FeedbackMessage
        fields = ("text",)
        widgets = {"text": forms.Textarea(attrs={"class": "form-control", "rows": 4})}
        error_messages = {"text": {"required": "Напишите, что хотели сказать."}}

    def __init__(self, *args, feedback, from_admin=False, **kwargs):
        self.feedback = feedback
        self.from_admin = from_admin
        super().__init__(*args, **kwargs)
        self.fields["text"].widget.attrs["maxlength"] = MESSAGE_MAX_LENGTH
        self.fields["text"].required = not from_admin

    def clean_text(self):
        return self.cleaned_data["text"].strip()

    def clean(self):
        cleaned = super().clean()
        # Лимит — только у автора: администратор отвечает сколько нужно.
        if not self.from_admin and sent_today(self.feedback.user) >= DAILY_LIMIT:
            raise forms.ValidationError(LIMIT_ERROR)
        return cleaned

    def save(self, commit=True):
        self.instance.feedback = self.feedback
        self.instance.from_admin = self.from_admin
        return super().save(commit=commit)


class FeedbackStatusForm(forms.ModelForm):
    """Статус обращения — у администратора рядом с полем ответа."""

    class Meta:
        model = Feedback
        fields = ("status",)
        widgets = {"status": forms.RadioSelect}
