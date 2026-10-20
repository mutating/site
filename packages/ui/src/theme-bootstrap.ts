import { initializeTheme } from "./theme-preference";

initializeTheme({ cookieDomain: (document.currentScript as HTMLScriptElement | null)?.dataset.tmcThemeCookieDomain });
