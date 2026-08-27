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
    const meta = document.querySelector('meta[name="theme-color"]');
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

  document.addEventListener("DOMContentLoaded", function () {
    apply(stored());
    document.querySelectorAll("[data-app-theme-toggle]").forEach(function (btn) {
      btn.addEventListener("click", window.appTheme.toggle);
    });
  });
})();
