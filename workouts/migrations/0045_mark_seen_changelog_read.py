# Метка «Новое» у новостей теперь гаснет по прочтению конкретной записи
# (ChangelogEntry.read_by). Всё, что человек уже видел в «Что нового», — вышедшее
# до его последнего открытия страницы (changelog_seen_at), — считаем прочитанным
# сразу: иначе после деплоя «Новое» зажглось бы на всей истории проекта, и гасить
# его пришлось бы по одной карточке.

from django.db import migrations


def mark_seen_as_read(apps, schema_editor):
    user_model = apps.get_model("accounts", "User")
    entry_model = apps.get_model("workouts", "ChangelogEntry")
    through = entry_model.read_by.through
    rows = [
        through(changelogentry_id=entry_id, user_id=user.pk)
        for user in user_model.objects.exclude(changelog_seen_at=None)
        for entry_id in entry_model.objects.filter(
            is_published=True, published_at__lte=user.changelog_seen_at
        ).values_list("pk", flat=True)
    ]
    through.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_user_weekly_goal"),
        ("workouts", "0044_changelogentry_read_by"),
    ]

    operations = [migrations.RunPython(mark_seen_as_read, migrations.RunPython.noop)]
