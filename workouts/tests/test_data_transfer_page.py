"""Экран «Экспорт и импорт»: доступ, форма загрузки, отчёт."""

import io
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from workouts import excel_import
from workouts.models import Workout
from workouts.tests.factories import SportFactory, StrengthSetFactory, WorkoutFactory
from workouts.tests.sheets import build_sheet

pytestmark = pytest.mark.django_db

PAGE = "data_transfer"


def upload(rows, name="история.xlsx"):
    return SimpleUploadedFile(name, build_sheet(rows).getvalue())


def test_page_requires_login(client):
    response = client.get(reverse(PAGE))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_page_shows_own_workout_count(client, user, other_user):
    WorkoutFactory(user=user)
    WorkoutFactory(user=user)
    WorkoutFactory(user=other_user)
    client.force_login(user)

    response = client.get(reverse(PAGE))

    assert response.status_code == 200
    assert response.context["workouts_count"] == 2


def test_page_has_upload_form(client, user):
    client.force_login(user)

    content = client.get(reverse(PAGE)).content.decode()

    assert 'enctype="multipart/form-data"' in content
    assert 'type="file"' in content


def test_empty_diary_says_so(client, user):
    client.force_login(user)

    content = client.get(reverse(PAGE)).content.decode()

    assert "Пока выгружать нечего" in content


def test_upload_creates_workouts_and_shows_report(client, user):
    SportFactory(name="Силовая")
    client.force_login(user)

    response = client.post(
        reverse(PAGE),
        {
            "file": upload(
                [
                    {
                        "date": date(2026, 9, 4),
                        "sport": "Силовая",
                        "exercise": "Жим лёжа",
                        "weight": 80,
                        "reps": 8,
                        "duration": 60,
                    }
                ]
            )
        },
    )

    assert response.status_code == 200
    assert response.context["report"].workouts == 1
    assert Workout.objects.filter(user=user).count() == 1
    assert "Что получилось" in response.content.decode()


def test_upload_without_file_shows_form_error(client, user):
    client.force_login(user)

    response = client.post(reverse(PAGE), {})

    assert response.status_code == 200
    assert "Выберите файл." in response.content.decode()
    assert not Workout.objects.exists()


def test_upload_of_foreign_format_is_explained(client, user):
    client.force_login(user)

    response = client.post(
        reverse(PAGE), {"file": SimpleUploadedFile("заметки.txt", b"not a workbook")}
    )

    assert response.status_code == 200
    assert ".xlsx" in response.content.decode()


def test_broken_workbook_does_not_give_500(client, user):
    """Файл с правильным расширением, но не книга — человеческий текст, не 500."""
    client.force_login(user)

    response = client.post(
        reverse(PAGE), {"file": SimpleUploadedFile("история.xlsx", b"PK\x03\x04 broken")}
    )

    assert response.status_code == 200
    assert "Не получилось открыть файл" in response.content.decode()


def test_too_big_file_is_rejected(client, user, monkeypatch):
    monkeypatch.setattr(excel_import, "MAX_UPLOAD_BYTES", 10)
    client.force_login(user)

    response = client.post(reverse(PAGE), {"file": SimpleUploadedFile("история.xlsx", b"x" * 100)})

    assert "Файл больше" in response.content.decode()
    assert not Workout.objects.exists()


def test_export_link_is_on_the_page(client, user):
    workout = WorkoutFactory(user=user)
    StrengthSetFactory(workout=workout)
    client.force_login(user)

    content = client.get(reverse(PAGE)).content.decode()

    assert reverse("workout_export") in content


def test_export_of_foreign_user_is_not_reachable(client, user, other_user):
    """Выгрузка отдаёт только свои тренировки — чужих в файле нет."""
    theirs = WorkoutFactory(user=other_user)
    StrengthSetFactory(workout=theirs)
    client.force_login(user)

    response = client.get(reverse("workout_export"))
    content = b"".join(response.streaming_content)

    from openpyxl import load_workbook

    sheet = load_workbook(io.BytesIO(content)).worksheets[0]
    assert sheet.max_row == 1
