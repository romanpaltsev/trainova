from django.conf import settings
from django.db import models
from django.utils import timezone

FEEDBACK_TEXT_MAX_LENGTH = 2000
MESSAGE_MAX_LENGTH = 2000


class Feedback(models.Model):
    """Обращение пользователя: идея, ошибка или вопрос — и переписка по нему.

    user — CASCADE: обращение — данные человека и уходит вместе с аккаунтом
    (accounts.deletion.delete_user_data ничего для этого делать не нужно).
    Первая реплика — text этой строки, всё дальнейшее — FeedbackMessage: список
    обращений и письмо админам читают одну строку, без джойна к сообщениям.
    user_seen_at — когда автор последний раз открывал обращение: ответ
    администратора новее этой метки горит точкой «есть ответ».
    admin_note и changelog_entry — инструменты администратора: заметка видна
    только в «Админке», новость показывается автору ссылкой, когда опубликована.
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
    user_seen_at = models.DateTimeField("автор открывал", null=True, blank=True)
    # Только для администраторов: в письма и на страницы автора не попадает.
    admin_note = models.TextField("заметка администратора", blank=True)
    # «Сделано — подробнее в „Что нового“». SET_NULL: снятая новость не должна
    # удалять обращение; неопубликованную автор и так не видит.
    changelog_entry = models.ForeignKey(
        "workouts.ChangelogEntry",
        verbose_name="новость",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "-created_at"], name="feedback_user_idx")]
        verbose_name = "обращение"
        verbose_name_plural = "обращения"

    def __str__(self):
        return f"{self.get_kind_display()}: {self.text[:60]}"


class FeedbackMessageQuerySet(models.QuerySet):
    def unread(self):
        """Ответы администратора, которых автор ещё не видел.

        «Не видел» — новее Feedback.user_seen_at или обращение не открывали
        вовсе. Одно определение на точку панели и метку в списке обращений.
        """
        return self.filter(from_admin=True).filter(
            models.Q(feedback__user_seen_at__isnull=True)
            | models.Q(created_at__gt=models.F("feedback__user_seen_at"))
        )


class FeedbackMessage(models.Model):
    """Реплика переписки после первой: ответ администратора или дописка автора.

    Автора-администратора не храним: пользователь видит «Ответ», а не имя
    (никакой социальности), а FK на админа связал бы его удаление с чужими
    обращениями. Сообщения не правятся — отправленное уже ушло письмом.
    """

    feedback = models.ForeignKey(
        Feedback, verbose_name="обращение", on_delete=models.CASCADE, related_name="messages"
    )
    from_admin = models.BooleanField("от администратора", default=False)
    text = models.TextField("текст", max_length=MESSAGE_MAX_LENGTH)
    created_at = models.DateTimeField("отправлено", default=timezone.now)

    objects = FeedbackMessageQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "сообщение"
        verbose_name_plural = "сообщения"

    def __str__(self):
        return self.text[:60]
