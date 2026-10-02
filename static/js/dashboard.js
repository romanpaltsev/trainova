// Дашборд: график «часы по неделям» и спарклайн карточки-прожектора.
(function () {
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
})();
