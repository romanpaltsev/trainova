// Общая обвязка Chart.js: цвета из CSS-токенов и перерисовка при смене темы.
// Canvas не понимает var(--…) — токены резолвятся в конкретные значения в момент
// отрисовки, а MutationObserver на data-bs-theme пере-стилизует все графики.

window.appCharts = (function () {
  const registry = [];

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function theme() {
    return {
      text: cssVar("--app-text"),
      muted: cssVar("--app-text-muted"),
      line: cssVar("--app-line"),
      card: cssVar("--app-card"),
      raised: cssVar("--app-raised"),
      sports: {
        strength: cssVar("--app-sport-strength"),
        bike: cssVar("--app-sport-bike"),
        run: cssVar("--app-sport-run"),
        ski: cssVar("--app-sport-ski"),
      },
    };
  }

  function register(chart, restyle) {
    registry.push({ chart, restyle });
  }

  new MutationObserver(function () {
    const t = theme();
    registry.forEach(function (entry) {
      if (!entry.chart.canvas.isConnected) return;
      entry.restyle(entry.chart, t);
      entry.chart.update("none");
    });
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-bs-theme"] });

  Chart.defaults.font.family = cssVar("--app-font-sans") || "system-ui, sans-serif";
  Chart.defaults.font.size = 12;

  function comma(value) {
    return String(value).replace(".", ",");
  }

  // 1.083 -> «1 ч 05 мин», 0.75 -> «45 мин»
  function hoursLong(value) {
    const total = Math.round(value * 60);
    const hours = Math.floor(total / 60);
    const minutes = total % 60;
    if (!hours) return `${minutes} мин`;
    return `${hours} ч ${String(minutes).padStart(2, "0")} мин`;
  }

  // Крайний (верхний или нижний) видимый ненулевой сегмент колонки стека.
  function edgeIndex(chart, column, fromTop) {
    const datasets = chart.data.datasets;
    const order = [...datasets.keys()];
    if (fromTop) order.reverse();
    for (const index of order) {
      if (chart.getDatasetMeta(index).hidden) continue;
      if (Number(datasets[index].data[column]) > 0) return index;
    }
    return -1;
  }

  function tooltipDefaults(t) {
    return {
      backgroundColor: t.raised,
      titleColor: t.text,
      bodyColor: t.text,
      borderColor: t.line,
      borderWidth: 1,
      padding: 10,
    };
  }

  // Stacked bar «часы по неделям» — по правилам графиков из CLAUDE.md.
  function buildStackedBar(canvas, payload) {
    const t = theme();
    const datasets = payload.datasets.map(function (d) {
      return {
        label: d.name,
        data: d.hours,
        stack: "hours",
        _colorKey: d.colorKey,
        backgroundColor: t.sports[d.colorKey],
        // Зазор 2px между сегментами: внутренняя нижняя рамка цветом карточки
        // у всех сегментов, кроме нижнего. borderSkipped:false обязателен —
        // иначе Chart.js пропускает именно нижнюю грань.
        borderColor: t.card,
        borderSkipped: false,
        borderWidth: function (ctx) {
          if (Number(ctx.dataset.data[ctx.dataIndex]) <= 0) return 0;
          if (ctx.datasetIndex === edgeIndex(ctx.chart, ctx.dataIndex, false)) return 0;
          return { bottom: 2 };
        },
        // Скруглён только верх стека — верхний ненулевой сегмент колонки.
        borderRadius: function (ctx) {
          if (ctx.datasetIndex !== edgeIndex(ctx.chart, ctx.dataIndex, true)) return 0;
          return { topLeft: 6, topRight: 6, bottomLeft: 0, bottomRight: 0 };
        },
        maxBarThickness: 34,
        categoryPercentage: 0.72,
        barPercentage: 1,
      };
    });

    const chart = new Chart(canvas, {
      type: "bar",
      data: { labels: payload.labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: { top: 8 } },
        onHover: function (event, elements) {
          event.native.target.style.cursor = elements.length ? "pointer" : "default";
        },
        onClick: function (event, elements, clicked) {
          const points = clicked.getElementsAtEventForMode(
            event, "index", { intersect: false }, true
          );
          if (!points.length) return;
          const url =
            clicked.canvas.dataset.weekUrl + "?start=" + payload.starts[points[0].index];
          htmx.ajax("GET", url, { target: "#week-detail", swap: "innerHTML show:#week-detail:top" });
        },
        scales: {
          x: {
            stacked: true,
            grid: { display: false },
            border: { display: true, color: t.line },
            ticks: {
              color: t.muted,
              font: { size: 11 },
              maxRotation: 0,
              autoSkip: true,
              autoSkipPadding: 16,
              includeBounds: true,
            },
          },
          y: {
            stacked: true,
            position: "right",
            beginAtZero: true,
            grid: { color: t.line, drawTicks: false },
            border: { display: false },
            ticks: {
              mirror: true,
              labelOffset: -8,
              color: t.muted,
              font: { size: 11 },
              stepSize: 2,
              maxTicksLimit: 5,
              callback: function (value) {
                return value > 0 ? value + " ч" : "";
              },
            },
          },
        },
        plugins: {
          // Легенда всегда есть, но HTML-ом в шапке карточки: токены тем бесплатно.
          legend: { display: false },
          tooltip: Object.assign(tooltipDefaults(t), {
            callbacks: {
              title: function (items) {
                return payload.titles ? payload.titles[items[0].dataIndex] : items[0].label;
              },
              label: function (ctx) {
                return ctx.dataset.label + ": " + hoursLong(ctx.parsed.y);
              },
            },
          }),
        },
      },
    });

    register(chart, function (target, next) {
      target.data.datasets.forEach(function (dataset) {
        dataset.backgroundColor = next.sports[dataset._colorKey];
        dataset.borderColor = next.card;
      });
      const scales = target.options.scales;
      scales.x.ticks.color = next.muted;
      scales.y.ticks.color = next.muted;
      scales.x.border.color = next.line;
      scales.y.grid.color = next.line;
      Object.assign(target.options.plugins.tooltip, tooltipDefaults(next));
    });
    return chart;
  }

  // Линия «максимальный вес» на странице упражнения.
  function buildLine(canvas, payload) {
    const t = theme();
    const color = t.sports[payload.colorKey] || t.sports.strength;
    const chart = new Chart(canvas, {
      type: "line",
      data: {
        labels: payload.labels,
        datasets: [
          {
            data: payload.values,
            _colorKey: payload.colorKey,
            borderColor: color,
            backgroundColor: color,
            pointBackgroundColor: color,
            borderWidth: 2,
            pointRadius: 3,
            tension: 0.3,
            fill: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: { top: 8 } },
        scales: {
          x: {
            grid: { display: false },
            border: { display: true, color: t.line },
            ticks: {
              color: t.muted,
              font: { size: 11 },
              maxRotation: 0,
              autoSkip: true,
              autoSkipPadding: 16,
              includeBounds: true,
            },
          },
          y: {
            position: "right",
            grid: { color: t.line, drawTicks: false },
            border: { display: false },
            ticks: {
              mirror: true,
              labelOffset: -8,
              color: t.muted,
              font: { size: 11 },
              maxTicksLimit: 5,
              callback: function (value) {
                return comma(value) + " " + payload.unit;
              },
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: Object.assign(tooltipDefaults(t), {
            callbacks: {
              label: function (ctx) {
                return comma(ctx.parsed.y) + " " + payload.unit;
              },
            },
          }),
        },
      },
    });

    register(chart, function (target, next) {
      const dataset = target.data.datasets[0];
      const nextColor = next.sports[dataset._colorKey] || next.sports.strength;
      dataset.borderColor = nextColor;
      dataset.backgroundColor = nextColor;
      dataset.pointBackgroundColor = nextColor;
      const scales = target.options.scales;
      scales.x.ticks.color = scales.y.ticks.color = next.muted;
      scales.x.border.color = scales.y.grid.color = next.line;
      Object.assign(target.options.plugins.tooltip, tooltipDefaults(next));
    });
    return chart;
  }

  // Спарклайн прожектора: без осей, точка только на последнем значении.
  function buildSparkline(canvas, values, colorKey) {
    const t = theme();
    const color = t.sports[colorKey] || t.sports.strength;
    const chart = new Chart(canvas, {
      type: "line",
      data: {
        labels: values.map(function (_, index) {
          return index;
        }),
        datasets: [
          {
            data: values,
            _colorKey: colorKey || "strength",
            borderColor: color,
            pointBackgroundColor: color,
            borderWidth: 2,
            tension: 0.3,
            pointRadius: function (ctx) {
              return ctx.dataIndex === values.length - 1 ? 3 : 0;
            },
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        events: [],
        layout: { padding: 4 },
        scales: { x: { display: false }, y: { display: false } },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
      },
    });

    register(chart, function (target, next) {
      const dataset = target.data.datasets[0];
      const nextColor = next.sports[dataset._colorKey] || next.sports.strength;
      dataset.borderColor = nextColor;
      dataset.pointBackgroundColor = nextColor;
    });
    return chart;
  }

  return { buildStackedBar, buildLine, buildSparkline };
})();
