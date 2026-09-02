// Живой режим: тикающая длительность тренировки и таймер отдыха.
// Таймеры всегда пересчитываются от абсолютных отметок времени: фоновая вкладка
// троттлит интервалы, и декрементный счётчик наврал бы после возврата.

function formatSeconds(total) {
  const pad = (value) => String(value).padStart(2, "0");
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}

// ---------- Степперы подхода ----------
//
// Значение меняется сразу по тапу, не дожидаясь ответа: на мобильной сети
// round-trip занимает сотни миллисекунд, а тапы складываются в очередь hx-sync,
// и без предсказания экран выглядел бы застывшим. Сервер остаётся источником
// истины — его ответ перезаписывает span. Шаг, максимум и формат приходят из
// data-атрибутов, чтобы у правил не было второй копии в JS.
const stepperValue = {
  // Обратное преобразование к тому, что показано: текст на экране и есть
  // текущее значение, поэтому отдельный data-raw не нужен и не устаревает.
  read(el) {
    const text = el.textContent.trim();
    if (el.dataset.format === "time") {
      const [minutes, seconds] = text.split(":");
      return Number(minutes) * 60 + Number(seconds || 0);
    }
    return parseFloat(text.replace(",", ".")) || 0;
  },

  format(el, raw) {
    if (el.dataset.format === "time") return formatSeconds(raw);
    if (el.dataset.format === "int") return String(raw);
    // Вес: до сотых, без хвостовых нулей, с запятой — как decimal_display.
    return String(Math.round(raw * 100) / 100).replace(".", ",");
  },

  predict(el, direction) {
    const step = parseFloat(el.dataset.step);
    const max = parseFloat(el.dataset.max);
    if (Number.isNaN(step) || Number.isNaN(max)) return null;
    const next = this.read(el) + (direction === "up" ? step : -step);
    // Плавающая точка: 82,5 + 2,5 иначе даст 84.99999999999999.
    return Math.round(Math.min(max, Math.max(0, next)) * 100) / 100;
  },
};

// Пометка «значение не доехало»: пока она висит, число на экране — предсказание,
// а не то, что в базе. Снимается следующим успешным ответом.
function markStale(valueEl, stale) {
  const field = valueEl.closest(".app-stepper-field");
  valueEl.classList.toggle("is-stale", stale);
  const notice = field && field.querySelector(".app-stepper-notice");
  if (notice) notice.hidden = !stale;
}

function stepperTarget(elt) {
  const field = elt.closest(".app-stepper-field");
  return field && field.querySelector(".app-stepper-value");
}

// Предсказание вешаем на сам тап, а не на htmx:beforeRequest: при серии тапов
// hx-sync откладывает запросы в очередь, beforeRequest для них наступит через
// секунды — и второй с третьим тапом выглядели бы «не нажались».
document.addEventListener("click", function (event) {
  const button = event.target.closest && event.target.closest(".app-stepper-btn[data-dir]");
  if (!button) return;
  const value = stepperTarget(button);
  const predicted = value && stepperValue.predict(value, button.dataset.dir);
  if (predicted !== null && predicted !== undefined) {
    value.textContent = stepperValue.format(value, predicted);
  }
});

document.addEventListener("htmx:afterRequest", function (event) {
  const elt = event.detail.elt;
  if (!elt.matches || !elt.matches(".app-stepper-btn[data-dir], .app-stepper-input")) return;
  const value = stepperTarget(elt);
  if (value) markStale(value, !event.detail.successful);
});

// Обрыв связи и таймаут из htmx-config приходят без ответа. Раньше такой запрос
// висел вечно и намертво блокировал очередь тапов — теперь очередь сдвигается,
// а значение честно помечается несохранённым.
["htmx:timeout", "htmx:sendError"].forEach(function (name) {
  document.addEventListener(name, function (event) {
    const elt = event.detail.elt;
    if (!elt.matches || !elt.matches(".app-stepper-btn[data-dir], .app-stepper-input")) return;
    const value = stepperTarget(elt);
    if (value) markStale(value, true);
  });
});

// Ручной ввод: тап по числу открывает поле на всю ширину — кнопки ±44px на это
// время скрываются, иначе на 375px полю осталось бы ~55px. Сам запрос
// декларативный (hx-post на поле), Alpine отвечает только за показ и ошибку.
function stepperInput() {
  return {
    editing: false,
    error: "",

    open() {
      this.editing = true;
      this.error = "";
      this.$nextTick(() => {
        const input = this.$refs.input;
        input.value = this.$refs.value.textContent.trim();
        input.focus();
        input.select();
      });
    },

    close() {
      this.editing = false;
      this.error = "";
    },

    // 400 приходит с человеческим текстом от сервера — его и показываем.
    failed(detail) {
      this.error = detail.xhr.responseText || "Не получилось сохранить";
    },
  };
}

// Звук и вибрация окончания отдыха. iOS разрешает звук только после жеста
// пользователя, поэтому контекст разблокируется первым тапом по странице;
// без жеста сигнал тихо не срабатывает — ожидаемая деградация.
const liveAudio = {
  ctx: null,

  unlock() {
    try {
      this.ctx = this.ctx || new (window.AudioContext || window.webkitAudioContext)();
      if (this.ctx.state === "suspended") this.ctx.resume();
    } catch (e) {
      /* WebAudio недоступен — молчим */
    }
  },

  beep() {
    if (!this.ctx || this.ctx.state !== "running") return;
    [0, 0.25].forEach((offset) => {
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.frequency.value = 880;
      osc.connect(gain).connect(this.ctx.destination);
      const start = this.ctx.currentTime + offset;
      gain.gain.setValueAtTime(0.001, start);
      gain.gain.exponentialRampToValueAtTime(0.3, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.18);
      osc.start(start);
      osc.stop(start + 0.2);
    });
  },
};

document.addEventListener("pointerdown", () => liveAudio.unlock(), { once: true });

// Забыть сохранённый отсчёт отдыха — вызывается из модалки завершения.
function clearRestTimer(workoutId) {
  try {
    localStorage.removeItem(`app-rest-${workoutId}`);
  } catch (e) {
    /* приватный режим */
  }
}

// «тренировка идёт · 42:16» в шапке живого экрана.
function liveClock() {
  return {
    text: "",

    init() {
      const started = Date.parse(this.$el.dataset.started);
      const update = () => {
        const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
        this.text = formatSeconds(seconds);
      };
      update();
      setInterval(update, 1000);
    },
  };
}

function restTimer() {
  return {
    running: false,
    duration: 90, // сохранённая длительность отдыха, сек
    total: 90, // длительность текущего отсчёта (с учётом разовых ±15)
    endTs: 0, // абсолютный момент окончания, мс
    remaining: 0,
    timerId: null,

    init() {
      // Не «|| 90»: ноль — валидное «без отдыха», а не повод взять дефолт.
      const parsed = parseInt(this.$el.dataset.duration, 10);
      this.duration = Number.isNaN(parsed) ? 90 : parsed;
      this.remaining = this.duration;
      this.storageKey = `app-rest-${this.$el.dataset.workout}`;
      this.onVisible = () => {
        // После троттлинга фоновой вкладки первый же тик показывает правду.
        if (!document.hidden && this.running) this.tick();
      };
      document.addEventListener("visibilitychange", this.onVisible);

      if (this.$el.dataset.autostart) {
        // «Подход выполнен»: отдых всегда начинается заново, даже если прошлый
        // отсчёт ещё тикал — сохранённый остаток здесь не восстанавливается.
        this.clearSaved();
        this.start(Date.now() + this.duration * 1000, this.duration);
        return;
      }
      const saved = this.restore();
      if (saved && saved.endTs > Date.now()) {
        // Перезагрузка страницы посреди отдыха: продолжаем с правильного места.
        this.start(saved.endTs, saved.total);
      } else {
        this.clearSaved();
      }
    },

    destroy() {
      // OOB-свап заменяет карточку целиком — интервал и подписку надо погасить.
      clearInterval(this.timerId);
      document.removeEventListener("visibilitychange", this.onVisible);
    },

    display() {
      return formatSeconds(this.running ? this.remaining : this.duration);
    },

    start(endTs, total) {
      this.endTs = endTs;
      this.total = Math.max(1, total);
      this.running = true;
      this.persist();
      clearInterval(this.timerId);
      this.timerId = setInterval(() => this.tick(), 250);
      this.tick();
    },

    tick() {
      if (!this.$el.isConnected) {
        clearInterval(this.timerId);
        return;
      }
      this.remaining = Math.max(0, Math.ceil((this.endTs - Date.now()) / 1000));
      if (this.$refs.bar) {
        this.$refs.bar.style.width = `${100 * (1 - this.remaining / this.total)}%`;
      }
      if (this.remaining <= 0) this.finish(true);
    },

    // Разовая подкрутка тикающего таймера — не сохраняется.
    bump(delta) {
      this.endTs += delta * 1000;
      this.total = Math.max(15, this.total + delta);
      if (this.endTs <= Date.now()) {
        this.finish(false); // докрутили до нуля руками — без сигнала
      } else {
        this.persist();
        this.tick();
      }
    },

    // Изменение сохранённой длительности (таймер не тикает): rest_seconds пишет
    // hx-post на той же кнопке, здесь — оптимистичное обновление и те же границы.
    adjust(delta) {
      this.duration = Math.max(15, Math.min(600, this.duration + delta));
    },

    skip() {
      this.stop();
    },

    finish(signal) {
      this.stop();
      if (signal) {
        liveAudio.beep();
        if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
      }
    },

    stop() {
      clearInterval(this.timerId);
      this.running = false;
      this.remaining = this.duration;
      if (this.$refs.bar) this.$refs.bar.style.width = "0%";
      this.clearSaved();
    },

    persist() {
      try {
        localStorage.setItem(
          this.storageKey,
          JSON.stringify({ endTs: this.endTs, total: this.total })
        );
      } catch (e) {
        /* приватный режим */
      }
    },

    restore() {
      try {
        return JSON.parse(localStorage.getItem(this.storageKey));
      } catch (e) {
        return null;
      }
    },

    clearSaved() {
      try {
        localStorage.removeItem(this.storageKey);
      } catch (e) {
        /* приватный режим */
      }
    },
  };
}

// ---------- Связь и возврат к экрану ----------
//
// Две беды живого экрана в зале, обе выглядят как «приложение не реагирует».
//
// 1. Неудачный запрос проходил молча. У степперов есть своя пометка, а у
//    «Подход выполнен», «+ Упражнение» и строк очереди — нет: тап просто
//    ничего не делал. Теперь любая осечка поднимает полосу с причиной.
// 2. iOS усыпляет страницу, пока телефон лежит в кармане между подходами. На
//    возврате таймеры стоят, запрос в полёте умирает без событий, а кнопка с
//    hx-disabled-elt остаётся выключенной — помогает только переход на другую
//    страницу и назад. Делаем это сами: после долгой паузы перезагружаем экран.

const RESUME_RELOAD_AFTER_MS = 60000;

function offlineBar() {
  return document.getElementById("live-offline");
}

function showOffline(text) {
  const bar = offlineBar();
  if (!bar) return;
  const label = document.getElementById("live-offline-text");
  if (label) label.textContent = text;
  bar.hidden = false;
}

function hideOffline() {
  const bar = offlineBar();
  if (bar) bar.hidden = true;
}

document.addEventListener("htmx:responseError", function (event) {
  const status = event.detail.xhr ? event.detail.xhr.status : 0;
  showOffline(`Ошибка сервера ${status} — изменение не сохранено.`);
});

["htmx:timeout", "htmx:sendError"].forEach(function (name) {
  document.addEventListener(name, function () {
    showOffline("Нет связи — изменение не сохранено.");
  });
});

// Полосу снимает любой удавшийся запрос: связь вернулась.
document.addEventListener("htmx:afterRequest", function (event) {
  if (event.detail.successful) hideOffline();
});

let hiddenAt = null;

document.addEventListener("visibilitychange", function () {
  if (document.hidden) {
    hiddenAt = Date.now();
    return;
  }
  const away = hiddenAt ? Date.now() - hiddenAt : 0;
  hiddenAt = null;
  if (away < RESUME_RELOAD_AFTER_MS) return;
  // Оффлайн перезагружать нельзя: вместо экрана будет ошибка браузера.
  if (navigator.onLine === false) {
    showOffline("Нет сети — экран мог устареть.");
    return;
  }
  window.location.reload();
});

// Возврат из кэша «назад/вперёд»: состояние страницы там заведомо устаревшее.
window.addEventListener("pageshow", function (event) {
  if (event.persisted) window.location.reload();
});
