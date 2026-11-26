(() => {
  "use strict";

  const root = document.documentElement;
  const THEME_KEY = "tmc-dropdown-header-designs-theme";
  const CHOICE_KEY = "tmc-dropdown-header-designs-choice";
  const number = n => String(n).padStart(2, "0");
  const cards = [...document.querySelectorAll(".design-card")].map(card => ({
    el: card,
    id: Number(card.dataset.id),
    title: card.dataset.title,
  }));

  /* Theme ---------------------------------------------------------------- */
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  let savedTheme = null;
  try { savedTheme = localStorage.getItem(THEME_KEY); } catch {}

  function applyTheme(theme, persist) {
    root.dataset.theme = theme;
    document.querySelectorAll("[data-theme-choice]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.themeChoice === theme));
    });
    if (persist) {
      try { localStorage.setItem(THEME_KEY, theme); } catch {}
    }
  }

  applyTheme(savedTheme === "dark" || savedTheme === "light" ? savedTheme : systemDark.matches ? "dark" : "light", false);
  document.querySelectorAll("[data-theme-choice]").forEach(button => {
    button.addEventListener("click", () => applyTheme(button.dataset.themeChoice, true));
  });
  systemDark.addEventListener("change", event => {
    let stored = null;
    try { stored = localStorage.getItem(THEME_KEY); } catch {}
    if (stored !== "dark" && stored !== "light") applyTheme(event.matches ? "dark" : "light", false);
  });

  /* Choice --------------------------------------------------------------- */
  let choice = null;
  try {
    const stored = Number(localStorage.getItem(CHOICE_KEY));
    if (Number.isInteger(stored) && cards.some(card => card.id === stored)) choice = stored;
  } catch {}

  function renderChoice() {
    const picked = cards.find(card => card.id === choice) || null;
    cards.forEach(card => {
      const selected = card.id === choice;
      card.el.classList.toggle("picked", selected);
      const pick = card.el.querySelector("[data-pick]");
      pick.setAttribute("aria-pressed", String(selected));
      pick.setAttribute("aria-label", `${selected ? "Снять выбор" : "Выбрать"} варианта ${number(card.id)}: ${card.title}`);
      pick.title = selected ? "Снять выбор" : "Выбрать этот вариант";
    });
    const status = document.getElementById("choice-status");
    status.classList.toggle("picked", Boolean(picked));
    status.textContent = picked ? `Выбор: ${number(picked.id)} · ${picked.title}` : "Выбор: —";
  }

  function choose(id) {
    choice = choice === id ? null : id;
    try {
      if (choice === null) localStorage.removeItem(CHOICE_KEY);
      else localStorage.setItem(CHOICE_KEY, String(choice));
    } catch {}
    renderChoice();
    const picked = cards.find(card => card.id === choice);
    if (picked) toast(`Выбран вариант ${number(picked.id)} · «${picked.title}»`);
  }

  /* Toast ---------------------------------------------------------------- */
  let toastTimer = 0;
  function toast(text) {
    const el = document.getElementById("toast");
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => el.classList.remove("show"), 2200);
  }

  /* Events --------------------------------------------------------------- */
  document.addEventListener("click", event => {
    const target = event.target.closest("button, a");
    if (!target) return;

    if (target.dataset.pick) {
      choose(Number(target.dataset.pick));
      return;
    }
    if (target.dataset.themeChoice) return;
    if (target.dataset.demo) {
      event.preventDefault();
      toast(target.dataset.demo === "exit"
        ? "Это макет: выход из аккаунта отключён"
        : "Это макет: переход в профиль отключён");
    }
  });

  renderChoice();
})();
