// Дашборд: график «часы по неделям», спарклайн карточки-прожектора и «Статистика».
(function () {
  // Ряд периодов на телефоне прокручивается вбок, и активный чип («Всё время»,
  // «Свой период») может стоять за правым краем — тогда в видимой части не горит
  // ни один. Подкручиваем ряд к нему; nearest не трогает вертикаль страницы, а
  // scroll-padding ряда оставляет справа тот же отступ, что слева.
  const activePeriod = document.querySelector("[data-period-chips] .is-active");
  if (activePeriod) activePeriod.scrollIntoView({ block: "nearest", inline: "nearest" });

  const chartEl = document.getElementById("weekly-chart");
  const dataEl = document.getElementById("chart-data");
  if (chartEl && dataEl) {
    const payload = JSON.parse(dataEl.textContent);
    if (payload.datasets.length) appCharts.buildStackedBar(chartEl, payload);
  }

  // Прожектор виден только на ПК (с 768, см. «Дашборд на средних экранах» в
  // app.css): Chart.js на скрытом canvas получает нулевой размер, поэтому
  // строим лениво по факту широкого вьюпорта. Порог обязан совпасть с CSS.
  const sparkEl = document.getElementById("spotlight-spark");
  const sparkData = document.getElementById("spark-data");
  if (sparkEl && sparkData) {
    const values = JSON.parse(sparkData.textContent);
    const wide = window.matchMedia("(min-width: 768px)");
    let built = false;
    const buildOnce = function () {
      if (built || !wide.matches || values.length < 2) return;
      built = true;
      appCharts.buildSparkline(sparkEl, values, "strength");
    };
    buildOnce();
    wide.addEventListener("change", buildOnce);
  }

  // «Статистика» за год (только ПК): карточка приходит htmx-ом, когда попадает в
  // поле зрения, и график строится по факту вставки. Вкладки переключают набор
  // данных без запросов: все три пришли разом. Первой открывается вкладка, где
  // есть данные, — у того, кто только бегает, «Время» всё равно есть всегда.
  htmx.onLoad(function (root) {
    const card = root.matches && root.matches("[data-stats]") ? root : null;
    const dataEl = card && card.querySelector("#stats-data");
    if (!dataEl) return;
    const payload = JSON.parse(dataEl.textContent);
    const canvas = card.querySelector("[data-stats-canvas]");
    const empty = card.querySelector("[data-stats-empty]");
    let chart = null;

    function show(key) {
      const tab = payload.tabs.find(function (item) {
        return item.key === key;
      });
      card.querySelectorAll("[data-stats-tab]").forEach(function (button) {
        const active = button.dataset.statsTab === key;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-selected", String(active));
      });
      card.querySelectorAll("[data-stats-panel]").forEach(function (panel) {
        panel.hidden = panel.dataset.statsPanel !== key;
      });
      appCharts.destroy(chart);
      chart = null;
      empty.hidden = tab.datasets.length > 0;
      canvas.parentElement.hidden = !tab.datasets.length;
      if (tab.datasets.length) chart = appCharts.buildArea(canvas, payload, tab);
    }

    card.querySelectorAll("[data-stats-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        show(button.dataset.statsTab);
      });
    });
    const first = payload.tabs.find(function (item) {
      return item.datasets.length;
    });
    show((first || payload.tabs[0]).key);
  });
})();
