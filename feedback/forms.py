from datetime import timedelta

from django import forms
from django.utils import timezone

from feedback.models import FEEDBACK_TEXT_MAX_LENGTH, REPLY_MAX_LENGTH, Feedback

# Мягкая защита от случайного спама (двойная отправка, залипшая кнопка), а не от
# злоумышленника: пишут сюда только вошедшие, друзья и родственники автора.
DAILY_LIMIT = 10


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
        since = timezone.now() - timedelta(days=1)
        if Feedback.objects.filter(user=self.user, created_at__gte=since).count() >= DAILY_LIMIT:
            raise forms.ValidationError(
                "За сутки можно отправить не больше 10 обращений — продолжим завтра."
            )
        return cleaned

    def save(self, commit=True):
        self.instance.user = self.user
        return super().save(commit=commit)


class FeedbackReplyForm(forms.ModelForm):
    """Ответ администратора: статус и текст. Письмо уходит, когда текст ответа меняется."""

    class Meta:
        model = Feedback
        fields = ("status", "reply")
        widgets = {
            "status": forms.RadioSelect,
            "reply": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reply"].widget.attrs["maxlength"] = REPLY_MAX_LENGTH

    def clean_reply(self):
        return self.cleaned_data["reply"].strip()
