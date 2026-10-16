"""Обработка загруженного фото: пережатие, поворот, метаданные, отказы."""

from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageCms

from uploads.forms import ImageUploadForm
from uploads.images import FULL_SIZE, MAX_UPLOAD_BYTES, THUMB_SIZE, process_image

# Теги EXIF: ориентация и ссылка на блок GPS.
ORIENTATION = 0x0112
GPS_IFD = 0x8825


def upload(image, fmt="JPEG", name="photo.jpg", **save):
    buffer = BytesIO()
    image.save(buffer, fmt, **save)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def opened(content_file):
    content_file.seek(0)
    return Image.open(BytesIO(content_file.read()))


def phone_photo(size=(400, 200), orientation=6):
    """Снимок «с телефона»: повёрнут тегом ориентации и с координатами съёмки."""
    exif = Image.Exif()
    exif[ORIENTATION] = orientation
    exif.get_ifd(GPS_IFD)[2] = (55.0, 45.0, 0.0)  # GPSLatitude
    return upload(Image.new("RGB", size, "red"), exif=exif.tobytes())


def test_large_photo_is_shrunk_to_full_and_thumb():
    result = process_image(upload(Image.new("RGB", (4000, 3000), "blue")))

    assert opened(result.full).size == (FULL_SIZE, 1200)
    assert opened(result.thumb).size == (THUMB_SIZE, 240)
    assert opened(result.full).format == "JPEG"


def test_small_photo_is_not_enlarged():
    result = process_image(upload(Image.new("RGB", (300, 200), "blue")))

    assert opened(result.full).size == (300, 200)


def test_orientation_is_applied_and_metadata_dropped():
    """Повёрнутый тегом снимок встаёт как надо, а EXIF с GPS не доезжает:
    фото общей модели видят все, координаты съёмки им ни к чему."""
    result = process_image(phone_photo())

    full = opened(result.full)
    assert full.size == (200, 400)
    assert not full.getexif()
    assert not opened(result.thumb).getexif()


def test_color_profile_is_kept():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()

    result = process_image(upload(Image.new("RGB", (50, 50)), icc_profile=profile))

    assert opened(result.full).info.get("icc_profile") == profile


def test_transparent_background_becomes_white():
    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))

    result = process_image(upload(image, "PNG", "cutout.png"))

    full = opened(result.full)
    assert full.mode == "RGB"
    assert all(channel > 245 for channel in full.getpixel((0, 0)))


def test_each_upload_gets_its_own_name():
    """Имя уникально на каждую загрузку: замена фото не перезаписывает файл,
    и вечный кэш браузера по адресу остаётся честным."""
    first = process_image(upload(Image.new("RGB", (10, 10))))
    second = process_image(upload(Image.new("RGB", (10, 10))))

    assert first.full.name != second.full.name
    assert first.full.name.endswith(".jpg")
    assert first.thumb.name == first.full.name.replace(".jpg", "-thumb.jpg")


@pytest.mark.parametrize(
    ("make", "code"),
    [
        (lambda: SimpleUploadedFile("note.jpg", b"not an image at all"), "unreadable"),
        (lambda: upload(Image.new("RGB", (10, 10)), "GIF", "anim.gif"), "format"),
        (lambda: SimpleUploadedFile("big.jpg", b"0" * (MAX_UPLOAD_BYTES + 1)), "too_large"),
        # 56 Мп в режиме «1 бит на пиксель» — крошечный файл, но в RGB это 170 МБ.
        (lambda: upload(Image.new("1", (8000, 7000)), "PNG", "bomb.png"), "too_many_pixels"),
    ],
)
def test_bad_files_are_refused(make, code):
    with pytest.raises(ValidationError) as error:
        process_image(make())

    assert error.value.code == code


def test_truncated_file_is_unreadable():
    whole = upload(Image.new("RGB", (200, 200), "green")).read()
    broken = SimpleUploadedFile("cut.jpg", whole[: len(whole) // 2])

    with pytest.raises(ValidationError) as error:
        process_image(broken)

    assert error.value.code == "unreadable"


def test_form_returns_processed_files():
    form = ImageUploadForm(files={"image": upload(Image.new("RGB", (2000, 1000)))})

    assert form.is_valid(), form.errors
    assert opened(form.cleaned_data["image"].full).size == (FULL_SIZE, 800)


def test_form_asks_for_a_file_and_explains_bad_ones():
    assert ImageUploadForm(files={}).errors["image"] == ["Выберите фото."]
    bad = ImageUploadForm(files={"image": SimpleUploadedFile("x.jpg", b"nope")})
    assert "JPEG, PNG или WebP" in bad.errors["image"][0]


def test_form_widget_enables_browser_resize_without_forcing_camera():
    html = str(ImageUploadForm()["image"])

    assert "data-resize" in html
    assert 'accept="image/*"' in html
    assert "capture" not in html
