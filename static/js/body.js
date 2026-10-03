// Мои замеры: график параметра на его странице. Каждая запись отвечает
// HX-Redirect и перезагружает страницу, поэтому пересборки после свопа нет.
(function () {
  const canvas = document.getElementById("metric-chart");
  const dataEl = document.getElementById("metric-chart-data");
  if (canvas && dataEl) appCharts.buildLine(canvas, JSON.parse(dataEl.textContent));
})();
