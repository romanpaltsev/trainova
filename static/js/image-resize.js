// Уменьшение фото перед загрузкой — у любого <input type="file" data-resize>.
//
// Снимок с телефона весит 3–8 МБ, сеть в зале медленная, а nginx не пускает
// больше 4 МБ. Браузер сам декодирует снимок (Safari — и HEIC), рисует его на
// canvas не больше MAX_SIDE по длинной стороне и подменяет файл в поле на JPEG;
// заодно пропадает EXIF с координатами съёмки. Сервер всё равно пережимает
// файл сам (uploads/images.py): здесь — скорость, там — гарантия. Любая осечка
// оставляет исходный файл: его проверит и пережмёт сервер.
//
// В форме поля по желанию: [data-resize-preview] — <img> для выбранного фото,
// [data-resize-error] — место для ошибки. Пока снимок обрабатывается, кнопки
// отправки формы выключены.
(function () {
  const MAX_SIDE = 1600;
  const QUALITY = 0.85;
  // Уже небольшой JPEG/PNG/WebP нужного размера не трогаем: пережатие только
  // потеряло бы качество.
  const KEEP_BYTES = 1024 * 1024;
  // Предел сервера и nginx — uploads.images.MAX_UPLOAD_BYTES.
  const MAX_BYTES = 4 * 1024 * 1024;
  const TOO_LARGE = "Файл больше 4 МБ — уменьшите фото.";

  function load(url) {
    return new Promise(function (resolve, reject) {
      const image = new Image();
      image.onload = function () {
        resolve(image);
      };
      image.onerror = reject;
      image.src = url;
    });
  }

  function toBlob(canvas) {
    return new Promise(function (resolve) {
      canvas.toBlob(resolve, "image/jpeg", QUALITY);
    });
  }

  async function shrink(file) {
    const url = URL.createObjectURL(file);
    try {
      // Ориентацию из EXIF браузер применяет сам: и к размерам, и к drawImage.
      const image = await load(url);
      const side = Math.max(image.naturalWidth, image.naturalHeight);
      const fits = side <= MAX_SIDE && file.size <= KEEP_BYTES;
      if (fits && /^image\/(jpeg|png|webp)$/.test(file.type)) return file;
      const scale = Math.min(1, MAX_SIDE / side);
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(image.naturalWidth * scale);
      canvas.height = Math.round(image.naturalHeight * scale);
      const context = canvas.getContext("2d");
      // У JPEG нет прозрачности: прозрачный фон — белый, как у сервера
      // (images.flatten), а не чёрный, как вышло бы по умолчанию.
      context.fillStyle = "white";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const blob = await toBlob(canvas);
      if (!blob) return file;
      const name = file.name.replace(/\.[^.]*$/, "") + ".jpg";
      return new File([blob], name, { type: "image/jpeg", lastModified: Date.now() });
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  function replaceFile(input, file) {
    try {
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
    } catch (error) {
      // Старый браузер без конструктора DataTransfer — уйдёт исходный файл.
    }
  }

  function setBusy(form, busy) {
    form.toggleAttribute("aria-busy", busy);
    form.querySelectorAll("[type=submit]").forEach(function (button) {
      button.disabled = busy;
    });
  }

  function showPreview(form, file) {
    const preview = form.querySelector("[data-resize-preview]");
    if (!preview) return;
    if (preview.dataset.objectUrl) URL.revokeObjectURL(preview.dataset.objectUrl);
    preview.dataset.objectUrl = URL.createObjectURL(file);
    preview.src = preview.dataset.objectUrl;
    preview.hidden = false;
  }

  function showError(form, text) {
    const error = form.querySelector("[data-resize-error]");
    if (!error) return;
    error.textContent = text;
    error.hidden = !text;
  }

  // Номер выбора: если человек выбрал другой снимок, пока первый ещё
  // обрабатывался, первый результат уже никому не нужен.
  let generation = 0;

  document.addEventListener("change", async function (event) {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.matches("input[type=file][data-resize]")) return;
    const form = input.form;
    const file = input.files && input.files[0];
    if (!form || !file) return;
    const current = ++generation;
    showError(form, "");
    setBusy(form, true);
    let result = file;
    try {
      result = await shrink(file);
    } catch (error) {
      // Не декодировалось (например, HEIC в Chrome) — пусть решает сервер.
    }
    if (current !== generation) return;
    if (result !== file) replaceFile(input, result);
    const chosen = input.files[0];
    if (chosen.size > MAX_BYTES) {
      // Кнопка остаётся выключенной: такой файл не пропустит nginx, и человек
      // увидел бы немой отказ вместо понятной ошибки.
      form.removeAttribute("aria-busy");
      showError(form, TOO_LARGE);
      return;
    }
    setBusy(form, false);
    showPreview(form, chosen);
  });
})();
