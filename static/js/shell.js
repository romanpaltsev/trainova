// Каркас ПК: свернуть и развернуть боковую панель (с 1200) и «/» — курсор в поиск
// шапки. Состояние панели живёт в localStorage и выставляется ещё в <head>
// (_page.html), иначе развёрнутая панель мигала бы на каждой странице.
(function () {
  const root = document.documentElement;
  const KEY = "app-sidebar";
  const toggle = document.querySelector("[data-sidebar-toggle]");

  function syncToggle() {
    if (toggle) toggle.setAttribute("aria-expanded", String(!root.hasAttribute("data-sidebar-collapsed")));
  }

  if (toggle) {
    toggle.addEventListener("click", function () {
      const collapsed = root.toggleAttribute("data-sidebar-collapsed");
      try {
        localStorage.setItem(KEY, collapsed ? "collapsed" : "expanded");
      } catch (e) {}
      syncToggle();
    });
    syncToggle();
  }

  // «/» — как в TailAdmin и почти любом веб-интерфейсе: курсор в поиск. Не
  // перехватываем, когда человек печатает в поле, и когда поиск скрыт (телефон).
  document.addEventListener("keydown", function (event) {
    if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
    const active = document.activeElement;
    if (active && (active.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName))) return;
    const search = document.querySelector("[data-header-search]");
    if (!search || search.offsetParent === null) return;
    event.preventDefault();
    search.focus();
  });
})();
