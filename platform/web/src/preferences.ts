let language = (navigator.languages || [navigator.language]).some(value => value.startsWith("ru")) ? "ru" : "en";
try {
  const stored = localStorage.getItem("tmc-language");
  if (stored === "ru" || stored === "en") language = stored;
} catch { /* Browser defaults remain available without storage. */ }
const query = new URLSearchParams(location.search);
const selected = query.get("tmc-language-choice");
if (selected === "ru" || selected === "en") {
  language = selected;
  try { localStorage.setItem("tmc-language", language); } catch { /* Optional persistence. */ }
  query.delete("tmc-language-choice");
  history.replaceState(history.state, "", `${location.pathname}${query.size ? `?${query}` : ""}${location.hash}`);
}
document.documentElement.lang = language;
