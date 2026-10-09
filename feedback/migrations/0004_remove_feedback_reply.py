from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("feedback", "0003_reply_to_message"),
    ]

    operations = [
        migrations.RemoveField(model_name="feedback", name="reply"),
        migrations.RemoveField(model_name="feedback", name="replied_at"),
    ]
