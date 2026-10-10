"""Письма о заявках в общий справочник: админам — о новой, автору — о решении.

Сбой почты не теряет заявку или решение: они уже в базе и видны в интерфейсе
(«Мои заявки», страница записи), поэтому ошибка SMTP только пишется в журнал —
приём feedback/notify.py.
"""

import logging

from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from feedback.notify import admin_emails

logger = logging.getLogger(__name__)


def _send(template, context, recipients):
    if not recipients:
        return
    subject = render_to_string(f"workouts/email/{template}_subject.txt", context).strip()
    body = render_to_string(f"workouts/email/{template}_message.txt", context)
    try:
        send_mail(subject, body, None, recipients)
    except Exception:  # noqa: BLE001 — любой сбой почты не повод терять заявку
        logger.exception("Письмо о заявке «%s» не отправлено", template)


def new_request(request, req):
    url = request.build_absolute_uri(reverse("admin_request_detail", args=[req.pk]))
    _send("request_new", {"req": req, "url": url}, admin_emails())


def request_decided(request, req):
    url = request.build_absolute_uri(reverse("my_requests"))
    _send("request_decided", {"req": req, "url": url}, [req.user.email])
