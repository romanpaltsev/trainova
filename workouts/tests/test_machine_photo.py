"""Фото модели тренажёра: загрузка, права, отдача файла, уборка.

Изоляция — главное: фото своей модели видит только хозяин (чужому — 404 и на
окно, и на сам файл), фото общей — все, администратор — ещё и фото модели из
заявки, по которому он решает.
"""

import os
import time
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from accounts.deletion import delete_user_data
from accounts.tests.factories import UserFactory
from uploads import sweep
from uploads.images import FULL_SIZE, process_image
from workouts import contributions
from workouts.tests.factories import (
    ExerciseFactory,
    LocationFactory,
    MachineBrandFactory,
    MachineModelFactory,
)

pytestmark = pytest.mark.django_db

DAY = 24 * 60 * 60


def jpeg(size=(2400, 1200), color="red", name="IMG_0001.jpg"):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def attach_photo(model):
    image = process_image(jpeg((400, 300)))
    model.photo.save(image.full.name, image.full, save=False)
    model.photo_thumb.save(image.thumb.name, image.thumb, save=False)
    model.save()
    return model


def photo_url(model):
    return reverse("machine_photo", args=[model.pk])


def age(path, seconds):
    moment = time.time() - seconds
    os.utime(path, (moment, moment))


@pytest.fixture
def own_model(user):
    brand = MachineBrandFactory(name="Свой зал", owner=user)
    return MachineModelFactory(brand=brand, name="Жим ногами 45°", owner=user)


@pytest.fixture
def shared_model(db):
    return MachineModelFactory(brand=MachineBrandFactory(name="Technogym"), name="Selection 900")


@pytest.fixture
def admin(db):
    return UserFactory(is_staff=True)


# ---------- Загрузка, замена, удаление ----------


def test_owner_uploads_photo_from_my_machines(client, user, own_model, media_root):
    client.force_login(user)

    response = client.post(photo_url(own_model), {"image": jpeg()})

    assert response.status_code == 200
    assert response["HX-Refresh"] == "true"
    own_model.refresh_from_db()
    assert own_model.photo.name.startswith("machines/")
    assert own_model.photo_thumb.name.endswith("-thumb.jpg")
    with Image.open(media_root / own_model.photo.name) as stored:
        assert stored.size == (FULL_SIZE, 800)
        assert not stored.getexif()


def test_upload_from_picker_returns_to_its_models(client, user, own_model):
    exercise = ExerciseFactory(name="Жим ногами", owner=None, equipment="Тренажёр")
    gym = LocationFactory(owner=user, name="СпортЛайф")
    client.force_login(user)

    response = client.post(
        photo_url(own_model),
        {"image": jpeg(), "back": "picker", "exercise": exercise.pk, "location": gym.pk},
    )

    own_model.refresh_from_db()
    html = response.content.decode()
    assert "HX-Refresh" not in response
    assert "Модель не знаю" in html  # снова шаг моделей окна «Тренажёр»
    assert own_model.thumb_url in html
    assert f'data-photo="{own_model.photo_url}"' in html


def test_picker_back_needs_own_location(client, user, other_user, own_model):
    exercise = ExerciseFactory(owner=None)
    foreign = LocationFactory(owner=other_user)
    client.force_login(user)

    response = client.get(
        photo_url(own_model), {"back": "picker", "exercise": exercise.pk, "location": foreign.pk}
    )

    assert response.status_code == 404


def test_bad_file_keeps_old_photo_and_explains(client, user, own_model):
    attach_photo(own_model)
    old = own_model.photo.name
    client.force_login(user)

    response = client.post(photo_url(own_model), {"image": SimpleUploadedFile("x.jpg", b"nope")})

    own_model.refresh_from_db()
    assert own_model.photo.name == old
    assert "JPEG, PNG или WebP" in response.content.decode()


def test_replace_and_remove_leave_files_to_the_nightly_sweep(client, user, own_model, media_root):
    """Окно не удаляет файлы само: путь удаления один — ночная уборка."""
    client.force_login(user)
    client.post(photo_url(own_model), {"image": jpeg()})
    own_model.refresh_from_db()
    first = media_root / own_model.photo.name

    client.post(photo_url(own_model), {"image": jpeg(color="blue")})
    own_model.refresh_from_db()
    second = media_root / own_model.photo.name
    assert second != first
    assert first.exists()

    client.post(photo_url(own_model), {"action": "remove"})
    own_model.refresh_from_db()
    assert not own_model.photo and not own_model.photo_thumb

    for path in (media_root / "machines").iterdir():
        age(path, 2 * DAY)
    assert sweep.sweep() == 4
    assert not any((media_root / "machines").iterdir())


def test_sweep_keeps_files_of_models_with_photo(user, own_model, media_root):
    attach_photo(own_model)
    for path in (media_root / "machines").iterdir():
        age(path, 30 * DAY)

    assert sweep.sweep() == 0
    assert (media_root / own_model.photo.name).exists()


def test_account_deletion_lets_sweep_remove_photos(user, own_model, media_root):
    attach_photo(own_model)
    files = list((media_root / "machines").iterdir())
    for path in files:
        age(path, 2 * DAY)

    delete_user_data(user)

    assert sweep.sweep() == 2
    assert not any(path.exists() for path in files)


# ---------- Права на окно ----------


def test_foreign_own_model_is_404(client, other_user, own_model):
    client.force_login(other_user)

    assert client.get(photo_url(own_model)).status_code == 404
    assert client.post(photo_url(own_model), {"image": jpeg()}).status_code == 404


def test_shared_model_photo_is_admins(client, user, admin, shared_model):
    client.force_login(user)
    assert client.get(photo_url(shared_model)).status_code == 404

    client.force_login(admin)
    html = client.get(photo_url(shared_model)).content.decode()
    assert "Фото увидят все" in html
    assert client.post(photo_url(shared_model), {"image": jpeg()})["HX-Refresh"] == "true"


# ---------- Отдача файла ----------


def test_owner_gets_file_with_long_private_cache(client, user, own_model):
    attach_photo(own_model)
    client.force_login(user)

    for url in (own_model.photo_url, own_model.thumb_url):
        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "image/jpeg"
        assert response["Cache-Control"] == "private, max-age=31536000, immutable"
        assert b"".join(response.streaming_content)[:2] == b"\xff\xd8"


def test_foreign_own_photo_is_404_and_guest_goes_to_login(client, other_user, own_model):
    attach_photo(own_model)

    assert client.get(own_model.photo_url).status_code == 302
    client.force_login(other_user)
    assert client.get(own_model.photo_url).status_code == 404
    assert client.get(own_model.thumb_url).status_code == 404


def test_unknown_file_is_404(client, user, own_model):
    client.force_login(user)

    url = reverse("machine_photo_file", args=["0123456789abcdef.jpg"])
    assert client.get(url).status_code == 404


def test_shared_photo_is_visible_to_everyone(client, other_user, shared_model):
    attach_photo(shared_model)
    client.force_login(other_user)

    assert client.get(shared_model.photo_url).status_code == 200


def test_admin_sees_photo_of_model_in_request_only(client, user, admin, own_model):
    attach_photo(own_model)
    client.force_login(admin)
    assert client.get(own_model.photo_url).status_code == 404

    req = contributions.propose(user, own_model)

    assert client.get(own_model.photo_url).status_code == 200
    html = client.get(reverse("admin_request_detail", args=[req.pk])).content.decode()
    assert own_model.photo_url in html
    assert "Фото станет видно всем" in html


def test_accepted_model_keeps_photo_for_everyone(client, user, other_user, admin, own_model):
    attach_photo(own_model)
    name = own_model.photo.name

    contributions.accept(contributions.propose(user, own_model))

    own_model.refresh_from_db()
    assert own_model.is_global and own_model.photo.name == name
    client.force_login(other_user)
    assert client.get(own_model.photo_url).status_code == 200


# ---------- Где видно ----------


def test_picker_shows_thumbs_zoom_and_camera_only_where_editable(client, user, shared_model):
    """У общего производителя — общая модель и своя: фотоаппарат только у своей,
    миниатюра — у той, где есть фото, заглушка — у остальных."""
    exercise = ExerciseFactory(name="Жим ногами", owner=None, equipment="Тренажёр")
    gym = LocationFactory(owner=user)
    own = attach_photo(MachineModelFactory(brand=shared_model.brand, name="Своя", owner=user))
    client.force_login(user)

    url = reverse("exercise_machine", args=[exercise.pk, gym.pk])
    html = client.get(url, {"brand": shared_model.brand.pk}).content.decode()

    assert own.thumb_url in html
    assert f'data-photo="{own.photo_url}"' in html
    assert "app-photo-thumb is-empty" in html  # у общей модели без фото
    assert "Заменить фото: Своя" in html
    assert f"фото: {shared_model.name}" not in html


def test_picker_without_photos_has_no_thumb_column(client, user, own_model):
    exercise = ExerciseFactory(owner=None)
    gym = LocationFactory(owner=user)
    client.force_login(user)

    url = reverse("exercise_machine", args=[exercise.pk, gym.pk])
    html = client.get(url, {"brand": own_model.brand.pk}).content.decode()

    assert "app-photo-thumb" not in html
    assert "Добавить фото: Жим ногами 45°" in html


def test_my_machines_rows_show_photo_slot(client, user, own_model):
    other = MachineModelFactory(brand=own_model.brand, name="Без фото", owner=user)
    attach_photo(own_model)
    client.force_login(user)

    html = client.get(reverse("my_machines")).content.decode()

    assert own_model.thumb_url in html
    assert f"Добавить фото: {other.name}" in html


def test_my_requests_show_model_thumb(client, user, own_model):
    attach_photo(own_model)
    contributions.propose(user, own_model)
    client.force_login(user)

    html = client.get(reverse("my_requests")).content.decode()

    assert own_model.thumb_url in html


def test_propose_window_warns_photo_becomes_public(client, user, own_model):
    attach_photo(own_model)
    client.force_login(user)

    html = client.get(reverse("catalog_propose", args=["model", own_model.pk])).content.decode()

    assert "Фото модели тоже увидят все" in html


def test_django_admin_cannot_upload_raw_photo(client, own_model):
    """В админке поля файла нет: там снимок не пережимался бы и сохранил EXIF."""
    superuser = UserFactory(is_staff=True, is_superuser=True)
    client.force_login(superuser)

    url = reverse("admin:workouts_machinemodel_change", args=[own_model.pk])
    html = client.get(url).content.decode()

    assert 'type="file"' not in html


def test_picker_budget_does_not_grow_with_photos(
    client, user, own_model, django_assert_max_num_queries
):
    exercise = ExerciseFactory(owner=None)
    gym = LocationFactory(owner=user)
    for number in range(5):
        attach_photo(MachineModelFactory(brand=own_model.brand, name=f"М{number}", owner=user))
    client.force_login(user)

    # Пара SAVEPOINT/RELEASE, сессия, пользователь, упражнение, место, тренажёр,
    # производитель, модели — фото в тех же строках, без запросов сверху.
    with django_assert_max_num_queries(9):
        client.get(
            reverse("exercise_machine", args=[exercise.pk, gym.pk]), {"brand": own_model.brand.pk}
        )
