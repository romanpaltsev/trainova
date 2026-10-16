from django import forms

from uploads.images import process_image


class ImageUploadForm(forms.Form):
    """Одно фото. После проверки в cleaned_data["image"] — уже пережатые
    файлы (images.ProcessedImage), а не исходник: сохранять исходник нечего.

    data-resize включает уменьшение в браузере (static/js/image-resize.js);
    capture нет намеренно — телефон предложит и камеру, и галерею.
    """

    image = forms.FileField(
        label="Фото",
        error_messages={"required": "Выберите фото.", "empty": "Файл пустой."},
        # app-file-input прячет поле визуально: нажимают подпись-кнопку вокруг
        # него, а фокус с клавиатуры остаётся на самом поле.
        widget=forms.FileInput(
            attrs={"accept": "image/*", "data-resize": "", "class": "app-file-input"}
        ),
    )

    def clean_image(self):
        return process_image(self.cleaned_data["image"])
