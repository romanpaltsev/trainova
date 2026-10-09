import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("feedback", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedback",
            name="user_seen_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="автор открывал"),
        ),
        migrations.CreateModel(
            name="FeedbackMessage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "from_admin",
                    models.BooleanField(default=False, verbose_name="от администратора"),
                ),
                ("text", models.TextField(max_length=2000, verbose_name="текст")),
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="отправлено"
                    ),
                ),
                (
                    "feedback",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="feedback.feedback",
                        verbose_name="обращение",
                    ),
                ),
            ],
            options={
                "verbose_name": "сообщение",
                "verbose_name_plural": "сообщения",
                "ordering": ["created_at", "id"],
            },
        ),
    ]
