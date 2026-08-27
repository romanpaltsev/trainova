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
      this.duration = parseInt(this.$el.dataset.duration, 10) || 90;
      this.remaining = this.duration;
      this.storageKey = `app-rest-${this.$el.dataset.workout}`;
      this.onVisible = () => {
        // После троттлинга фоновой вкладки первый же тик показывает правду.
        if (!document.hidden && this.running) this.tick();
      };
      document.addEventListener("visibilitychange", this.onVisible);

      const saved = this.restore();
      if (saved && saved.endTs > Date.now()) {
        // Перезагрузка страницы посреди отдыха: продолжаем с правильного места.
        this.start(saved.endTs, saved.total);
      } else {
        this.clearSaved();
        if (this.$el.dataset.autostart) {
          this.start(Date.now() + this.duration * 1000, this.duration);
        }
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
