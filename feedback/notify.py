"""Письма обратной связи: админам — о новом обращении и дописке, автору — об ответе.

Сбой почты не должен терять обращение или ответ: они уже в базе и видны в
интерфейсе, поэтому ошибка SMTP только пишется в журнал.
"""

import logging

from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from accounts.models import User

logger = logging.getLogger(__name__)


def admin_emails():
    """Адресаты — администраторы из базы (User.is_admin), без отдельной настройки."""
    admins = User.objects.filter(is_active=True).exclude(email="")
    return [user.email for user in admins if user.is_admin]


def _send(template, context, recipients):
    if not recipients:
        return
    subject = render_to_string(f"feedback/email/{template}_subject.txt", context).strip()
    body = render_to_string(f"feedback/email/{template}_message.txt", context)
    try:
        send_mail(subject, body, None, recipients)
    except Exception:  # noqa: BLE001 — любой сбой почты не повод терять обращение
        logger.exception("Письмо обратной связи «%s» не отправлено", template)


def new_feedback(request, feedback):
    url = request.build_absolute_uri(reverse("admin_feedback_detail", args=[feedback.pk]))
    _send("new_feedback", {"feedback": feedback, "url": url}, admin_emails())


def followup(request, message):
    feedback = message.feedback
    url = request.build_absolute_uri(reverse("admin_feedback_detail", args=[feedback.pk]))
    context = {"feedback": feedback, "message": message, "url": url}
    _send("followup", context, admin_emails())


def reply_sent(request, message):
    feedback = message.feedback
    url = request.build_absolute_uri(reverse("feedback_detail", args=[feedback.pk]))
    context = {"feedback": feedback, "message": message, "url": url}
    _send("reply", context, [feedback.user.email])
