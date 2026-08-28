// Страница упражнения: линия максимального веса по тренировкам.
(function () {
  const canvas = document.getElementById("exercise-chart");
  const dataEl = document.getElementById("exercise-chart-data");
  if (!canvas || !dataEl) return;
  appCharts.buildLine(canvas, JSON.parse(dataEl.textContent));
})();
