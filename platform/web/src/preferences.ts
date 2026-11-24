import wordmarkFont from "@mutating/ui/assets/fonts/science-gothic-wordmark.woff2?url";

// Match the static site: start the wordmark font early and keep the fallback
// wordmark hidden until the real face is ready.
const wordmarkFontPreload = document.createElement("link");
wordmarkFontPreload.rel = "preload";
wordmarkFontPreload.as = "font";
wordmarkFontPreload.type = "font/woff2";
wordmarkFontPreload.crossOrigin = "anonymous";
wordmarkFontPreload.href = wordmarkFont;
document.head.append(wordmarkFontPreload);
const root = document.documentElement;
root.dataset.wordmarkFontReady = "false";
if (!document.fonts || typeof document.fonts.load !== "function") {
  root.dataset.wordmarkFontReady = "fallback";
} else {
  requestAnimationFrame(() => {
    document.fonts.load('780 35px "Science Gothic"', "TMC")
      .then((faces) => {
        root.dataset.wordmarkFontReady = faces.length ? "true" : "fallback";
      })
      .catch(() => {
        root.dataset.wordmarkFontReady = "fallback";
      });
  });
}

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
