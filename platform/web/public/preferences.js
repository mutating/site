(() => {
  let theme = matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
  let language = (navigator.languages || [navigator.language]).some((x) =>
    x.startsWith("ru"),
  )
    ? "ru"
    : "en";
  try {
    const storedTheme = localStorage.getItem("tmc-theme");
    const storedLanguage = localStorage.getItem("tmc-language");
    if (["dark", "light"].includes(storedTheme)) theme = storedTheme;
    if (["ru", "en"].includes(storedLanguage)) language = storedLanguage;
  } catch {}
  document.documentElement.dataset.theme = theme;
  document.documentElement.lang = language;
})();
