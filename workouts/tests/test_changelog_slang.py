"""Вышедшие новости «Что нового» переписаны сленгом (миграция 0099_slang_changelog).

Анонсы приезжают data-миграциями, поэтому в тестовой базе они уже лежат в
итоговом виде — здесь проверяется, что каждый переписан и ничего не потерялось.
"""

import importlib
import re

import pytest
from django.apps import apps

from workouts.management.commands.seed import CHANGELOG
from workouts.models import ChangelogEntry

slang = importlib.import_module("workouts.migrations.0099_slang_changelog")

# Стартовые записи заводит seed, а не миграции: в тестовой базе их нет.
SEEDED = {title for _kind, title, _body, _day in CHANGELOG}
SHIPPED = [row for row in slang.REWRITES if row[2] not in SEEDED]


@pytest.mark.django_db
def test_every_shipped_announcement_is_rewritten():
    for old_title, _old_body, title, body in SHIPPED:
        assert ChangelogEntry.objects.filter(title=title, body=body).exists(), title
        assert not ChangelogEntry.objects.filter(title=old_title).exists(), old_title


def test_new_titles_are_unique():
    titles = [row[2] for row in slang.REWRITES]

    assert len(set(titles)) == len(titles)


def test_seed_starts_with_the_rewritten_texts():
    """Свежая база получает новые тексты сразу из seed, а прод — миграцией:
    тексты обязаны совпасть, иначе повторный seed завёл бы дубль."""
    rewritten = {title: body for _old, _old_body, title, body in slang.REWRITES}

    for _kind, title, body, _day in CHANGELOG:
        assert rewritten[title] == body


@pytest.mark.django_db
def test_rewrite_goes_back_and_forth():
    old_title, old_body, title, body = SHIPPED[0]
    entry = ChangelogEntry.objects.get(title=title)
    published_at = entry.published_at

    slang.rewrite_back(apps, None)
    entry.refresh_from_db()
    assert (entry.title, entry.body) == (old_title, old_body)

    slang.rewrite(apps, None)
    entry.refresh_from_db()
    assert (entry.title, entry.body) == (title, body)
    assert entry.published_at == published_at, "точка «есть новое» не должна загореться"


@pytest.mark.django_db
def test_entry_edited_in_admin_is_left_alone():
    """Совпадение и по заголовку, и по тексту: поправленную в админке запись
    миграция не трогает."""
    _old_title, _old_body, title, body = SHIPPED[1]
    ChangelogEntry.objects.filter(title=title).update(body="Правка администратора.")

    slang.rewrite_back(apps, None)

    assert ChangelogEntry.objects.get(title=title).body == "Правка администратора."


def test_no_emoji_and_no_invisible_spaces():
    """Эмодзи в интерфейсе запрещены, а неразрывный пробел в тексте не видно."""
    emoji = re.compile(r"[\U0001F000-\U0001FAFF☀-➿]")
    for row in slang.REWRITES:
        for text in row[2:]:
            assert not emoji.search(text), text
            assert "\xa0" not in text, text
            assert "  " not in text, text
