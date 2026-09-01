// Экран упражнений: график прогресса и мастер-деталь на широком экране.
//
// На десктопе клик по строке списка подгружает упражнение в правую панель, не
// уходя со страницы. На мобильном перехватчик молча выходит, и строка работает
// обычной ссылкой на отдельную страницу — экран остаётся рабочим и без JS.
(function () {
  const WIDE = window.matchMedia("(min-width: 1200px)");
  const panel = document.getElementById("exercise-panel");
  // Снимок пустого состояния: с ним «назад» в исходное состояние не требует
  // запроса, а русский текст не приходится дублировать в JS.
  const emptyPanel = panel ? panel.innerHTML : "";
  let chart = null;

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
      // У плитки хук стоит на самой ссылке, у строки — на обёртке рядом с кнопкой
      // удаления. Одно упражнение может быть на экране дважды (плиткой и строкой),
      // и подсвечиваются оба: справочник намеренно остался полным.
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
        renderChart();
        markActive(url);
        // Страница прокручивается целиком, поэтому клик по строке в низу списка
        // свапнул бы панель выше кромки экрана. scroll-margin-top не даёт ей
        // заехать под липкую шапку сайта.
        if (panel.getBoundingClientRect().top < 0) panel.scrollIntoView();
        const holder = panel.querySelector(".app-exercise");
        if (holder && holder.dataset.title) document.title = holder.dataset.title;
      });
  }

  function showEmpty() {
    panel.innerHTML = emptyPanel;
    if (chart) {
      appCharts.destroy(chart);
      chart = null;
    }
    markActive(null);
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
      const link = event.target.closest(LINK);
      if (!link) return;
      const url = link.getAttribute("href");
      if (!url) return;
      event.preventDefault();
      history.pushState({ exercisePanel: url }, "", url);
      loadPanel(url);
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
