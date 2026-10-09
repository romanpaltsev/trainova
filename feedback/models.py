from django.conf import settings
from django.db import models

FEEDBACK_TEXT_MAX_LENGTH = 2000
REPLY_MAX_LENGTH = 2000


class Feedback(models.Model):
    """Обращение пользователя: идея, ошибка или вопрос — и ответ администратора.

    user — CASCADE: обращение — данные человека и уходит вместе с аккаунтом
    (accounts.deletion.delete_user_data ничего для этого делать не нужно).
    Ответ живёт в той же строке, а не отдельной перепиской: обращение — одна
    реплика и один ответ, а за уточнением пишут новое.
    """

    class Kind(models.TextChoices):
        IDEA = "idea", "Идея"
        BUG = "bug", "Ошибка"
        QUESTION = "question", "Вопрос"

    class Status(models.TextChoices):
        NEW = "new", "Новое"
        IN_PROGRESS = "in_progress", "В работе"
        DONE = "done", "Готово"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="пользователь",
        on_delete=models.CASCADE,
        related_name="feedback",
    )
    kind = models.CharField("тип", max_length=16, choices=Kind.choices, default=Kind.IDEA)
    text = models.TextField("текст", max_length=FEEDBACK_TEXT_MAX_LENGTH)
    created_at = models.DateTimeField("отправлено", auto_now_add=True)
    status = models.CharField("статус", max_length=16, choices=Status.choices, default=Status.NEW)
    reply = models.TextField("ответ", max_length=REPLY_MAX_LENGTH, blank=True)
    replied_at = models.DateTimeField("ответ дан", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "-created_at"], name="feedback_user_idx")]
        verbose_name = "обращение"
        verbose_name_plural = "обращения"

    def __str__(self):
        return f"{self.get_kind_display()}: {self.text[:60]}"
