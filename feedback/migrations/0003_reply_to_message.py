"""Прежний единственный ответ (Feedback.reply) становится первым сообщением переписки."""

from django.db import migrations


def reply_to_message(apps, schema_editor):
    Feedback = apps.get_model("feedback", "Feedback")
    FeedbackMessage = apps.get_model("feedback", "FeedbackMessage")
    FeedbackMessage.objects.bulk_create(
        FeedbackMessage(
            feedback=item,
            from_admin=True,
            text=item.reply,
            created_at=item.replied_at or item.created_at,
        )
        for item in Feedback.objects.exclude(reply="")
    )


def message_to_reply(apps, schema_editor):
    Feedback = apps.get_model("feedback", "Feedback")
    for item in Feedback.objects.all():
        last = item.messages.filter(from_admin=True).order_by("-created_at", "-id").first()
        if last:
            item.reply, item.replied_at = last.text, last.created_at
            item.save(update_fields=["reply", "replied_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("feedback", "0002_feedbackmessage"),
    ]

    operations = [migrations.RunPython(reply_to_message, message_to_reply)]
