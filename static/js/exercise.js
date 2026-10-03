// Экран упражнений: график прогресса и шторка упражнения на широком экране.
//
// На ПК справочник — таблица во всю ширину; с 1200 клик по строке подгружает
// упражнение в шторку справа поверх таблицы (класс is-open на каталоге), не
// уходя со страницы, — таблица не сжимается и не теряет прокрутку. Крестик, Esc
// и «назад» шторку закрывают. Ниже 1200 и на телефоне перехватчик молча выходит,
// и строка работает обычной ссылкой на отдельную страницу — экран остаётся
// рабочим и без JS.
(function () {
  const WIDE = window.matchMedia("(min-width: 1200px)");
  const panel = document.getElementById("exercise-panel");
  // Раскладку переключает класс, а не :has() в CSS: без поддержки :has панель
  // осталась бы скрытой, и клик по упражнению ничего бы не показал.
  const catalog = document.querySelector("[data-exercise-catalog]");
  // Адрес и заголовок списка (вместе с фильтрами) — сюда возвращает крестик.
  // Заголовок запоминаем, а не пишем текстом: шаблон остаётся единственным местом.
  const listUrl = location.href;
  const listTitle = document.title;
  let chart = null;
  // Ссылка строки, с которой открыли шторку: при закрытии фокус возвращается
  // туда, откуда человек пришёл, а не в начало страницы.
  let opener = null;

  function renderChart() {
    if (chart) {
      appCharts.destroy(chart);
      chart = null;
    }
    const canvas = document.getElementById("exercise-chart");
    const dataEl = document.getElementById("exercise-chart-data");
    if (!canvas || !dataEl) return;
    // Скрытый canvas получает нулевой размер, и график собрался бы пустым —
    // тот же приём, что у спарклайна прожектора на дашборде.
    if (canvas.offsetParent === null) return;
    chart = appCharts.buildLine(canvas, JSON.parse(dataEl.textContent));
  }

  // Список общается с JS через data-атрибуты, а не через классы: классы — стилевые
  // хуки и меняются вместе с вёрсткой, а плитка и строка должны одинаково попадать
  // в мастер-деталь. Контейнер в селекторе не участвует намеренно: раскладка левой
  // колонки меняется, а поведение от неё зависеть не должно.
  const ITEM = "[data-exercise-item]";
  const LINK = "[data-exercise-link]";

  function markActive(url) {
    document.querySelectorAll(ITEM).forEach(function (item) {
      // У плитки хук стоит на самой ссылке, у строки группы и таблицы — на
      // обёртке рядом с кнопкой удаления. Телефонная разметка (плитки, группы)
      // остаётся в странице и на ПК спрятана: подсветка спрятанных ничего не
      // стоит, а видна только строка таблицы.
      const link = item.matches(LINK) ? item : item.querySelector(LINK);
      const active = Boolean(link) && Boolean(url) && link.getAttribute("href") === url;
      item.classList.toggle("is-active", active);
      if (active) {
        link.setAttribute("aria-current", "true");
      } else if (link) {
        link.removeAttribute("aria-current");
      }
    });
  }

  function loadPanel(url) {
    return htmx
      .ajax("GET", url, { target: "#exercise-panel", swap: "innerHTML" })
      .then(function () {
        // Сначала раскрыть шторку, потом строить график: на скрытом canvas
        // renderChart молча выходит.
        if (catalog) catalog.classList.add("is-open");
        // Шторка прокручивается сама, и новое упражнение открывается с начала,
        // а не на месте, где читали предыдущее.
        panel.scrollTop = 0;
        renderChart();
        markActive(url);
        // Фокус — в шторку: читалка начинает с открытого упражнения, а Tab
        // ведёт по нему, а не по таблице. Прокрутку страницы фокус не трогает.
        panel.focus({ preventScroll: true });
        const holder = panel.querySelector(".app-exercise");
        if (holder && holder.dataset.title) document.title = holder.dataset.title;
      });
  }

  // Переименование отвечает телом страницы, которое свапает само себя (OOB), —
  // и на отдельной странице, и внутри панели. Пересборка графика обязательна:
  // Chart.js держит старый canvas, а тот после свопа уже выброшен из документа.
  function afterBodySwap(body) {
    renderChart();
    if (body.dataset.title) document.title = body.dataset.title;
    const title = body.querySelector(".app-page-title");
    if (!body.dataset.url || !title) return;
    // Список слева целиком не перерисовываем: достаточно строки и плитки,
    // которые ссылаются на это упражнение. Иначе имя в списке разошлось бы с
    // заголовком до перезагрузки страницы.
    const name = title.textContent.trim();
    document.querySelectorAll(LINK).forEach(function (link) {
      if (link.getAttribute("href") !== body.dataset.url) return;
      const label = link.querySelector(".app-trained-name, .app-row-name");
      if (label) label.textContent = name;
    });
  }

  document.body.addEventListener("htmx:oobAfterSwap", function (event) {
    if (event.detail.target && event.detail.target.id === "exercise-body") {
      // При outerHTML-свопе target — уже выброшенный старый элемент: имя из него
      // отставало на одно переименование. Читаем то, что теперь в документе.
      afterBodySwap(document.getElementById("exercise-body") || event.detail.target);
    }
  });

  function showEmpty() {
    const focusInside = panel.contains(document.activeElement);
    panel.replaceChildren();
    if (chart) {
      appCharts.destroy(chart);
      chart = null;
    }
    markActive(null);
    if (catalog) catalog.classList.remove("is-open");
    // Фокус был в шторке — вернуть его на строку, с которой её открыли: иначе он
    // упал бы в начало страницы, и с клавиатуры пришлось бы идти по таблице заново.
    if (focusInside && opener && opener.isConnected) opener.focus({ preventScroll: true });
  }

  // Закрыть панель — вернуться к списку во всю ширину. Новой записью в истории,
  // а не history.back(): после нескольких открытых упражнений «назад» вело бы
  // к предыдущему упражнению, а не к списку.
  function closePanel() {
    history.pushState({ exercisePanel: null }, "", listUrl);
    showEmpty();
    document.title = listTitle;
  }

  if (panel) {
    // Исходное состояние тоже кладём в историю — иначе «назад» из первого
    // выбранного упражнения нечем опознать.
    history.replaceState({ exercisePanel: null }, "", location.href);

    document.addEventListener("click", function (event) {
      if (!WIDE.matches) return;
      // Модификаторы и не левая кнопка — это «открыть в новой вкладке»,
      // перехватывать такое нельзя.
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      if (event.target.closest("[data-exercise-close]")) {
        closePanel();
        return;
      }
      const link = event.target.closest(LINK);
      if (!link) return;
      const url = link.getAttribute("href");
      if (!url) return;
      event.preventDefault();
      opener = link;
      // Повторный клик по уже открытому — без второй записи в истории.
      if (catalog && catalog.classList.contains("is-open") && history.state && history.state.exercisePanel === url) {
        panel.focus({ preventScroll: true });
        return;
      }
      history.pushState({ exercisePanel: url }, "", url);
      loadPanel(url);
    });

    // Строка таблицы кликабельна целиком: клик по любой ячейке — это клик по
    // ссылке-названию. Синтетический клик проходит через перехватчик выше,
    // поэтому с 1200 откроется шторка, а ниже — обычный переход на страницу.
    // Псевдоэлемент на всю строку вместо этого не годится: Safari не делает
    // <tr> якорем для position, и оверлей накрыл бы всю таблицу.
    document.addEventListener("click", function (event) {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      // Свои клики у ссылок, кнопок и полей; выделение текста — не клик.
      if (event.target.closest("a, button, input, label, select, textarea")) return;
      const row = event.target.closest("[data-exercise-row]");
      if (!row || String(window.getSelection())) return;
      const link = row.querySelector(LINK);
      if (link) link.click();
    });

    // Esc закрывает панель — но не поверх модалки (переименование): ту Esc
    // закрывает сама, а слушатель документа срабатывает раньше её слушателя окна.
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !WIDE.matches) return;
      if (!catalog || !catalog.classList.contains("is-open")) return;
      if (document.querySelector("#modal .app-modal-backdrop")) return;
      closePanel();
    });

    window.addEventListener("popstate", function (event) {
      if (!WIDE.matches) return;
      const url = event.state && event.state.exercisePanel;
      if (url) {
        loadPanel(url);
      } else {
        showEmpty();
      }
    });

    // Ошибка ответа: панель осталась бы с прежним содержимым без объяснений.
    // Отдаём человека серверной странице ошибки — заодно закрывает случай
    // «упражнение удалили в другой вкладке».
    document.body.addEventListener("htmx:responseError", function (event) {
      if (event.detail.target && event.detail.target.id === "exercise-panel") {
        window.location.assign(event.detail.pathInfo.requestPath);
      }
    });
  }

  // Отдельная страница упражнения: график собирается сразу.
  renderChart();
  // Окно сузили и вернули — canvas был скрыт, график надо собрать заново.
  WIDE.addEventListener("change", renderChart);
})();
