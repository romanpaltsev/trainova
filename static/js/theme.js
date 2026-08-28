// Переключатель темы. Явный выбор пользователя живёт в localStorage["app-theme"],
// значение "system" означает «следовать за prefers-color-scheme».
(function () {
  const KEY = "app-theme";
  const media = window.matchMedia("(prefers-color-scheme: dark)");

  function resolve(choice) {
    if (choice === "light" || choice === "dark") return choice;
    return media.matches ? "dark" : "light";
  }

  function apply(choice) {
    const theme = resolve(choice);
    document.documentElement.setAttribute("data-bs-theme", theme);
    const meta = document.getElementById("theme-color");
    if (meta) {
      meta.setAttribute(
        "content",
        getComputedStyle(document.documentElement).getPropertyValue("--app-bg").trim()
      );
    }
  }

  function stored() {
    try {
      return localStorage.getItem(KEY) || "system";
    } catch (e) {
      return "system";
    }
  }

  function store(choice) {
    try {
      localStorage.setItem(KEY, choice);
    } catch (e) {
      /* приватный режим — тема просто не запомнится */
    }
  }

  window.appTheme = {
    get: stored,
    set: function (choice) {
      store(choice);
      apply(choice);
    },
    toggle: function () {
      const next = resolve(stored()) === "dark" ? "light" : "dark";
      window.appTheme.set(next);
    },
  };

  media.addEventListener("change", function () {
    if (stored() === "system") apply("system");
  });

  // Трёхпозиционный выбор темы в профиле: светлая / тёмная / системная.
  // Серверу выбор неизвестен — он живёт в localStorage, поэтому и предвыбор здесь.
  function bindThemeChoice() {
    document.querySelectorAll("[data-app-theme-choice]").forEach(function (group) {
      var choice = stored();
      group.querySelectorAll('input[type="radio"]').forEach(function (radio) {
        radio.checked = radio.value === choice;
        radio.addEventListener("change", function () {
          if (radio.checked) window.appTheme.set(radio.value);
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindThemeChoice();
    apply(stored());
    document.querySelectorAll("[data-app-theme-toggle]").forEach(function (btn) {
      btn.addEventListener("click", window.appTheme.toggle);
    });
  });
})();
