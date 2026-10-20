import type { Theme } from "./theme";

export interface ThemePreferenceOptions {
  storageKey?: string;
  /** Share explicit choices on this domain and its subdomains. Ignored on other hosts. */
  cookieDomain?: string;
}

export const DEFAULT_THEME_STORAGE_KEY = "tmc-theme";
export const isTheme = (value: unknown): value is Theme => value === "light" || value === "dark";

function cookieDomain(window: Window, domain?: string): string | undefined {
  const hostname = window.location.hostname;
  return domain && (hostname === domain || hostname.endsWith(`.${domain}`)) ? domain : undefined;
}

export function writeThemePreference(window: Window, theme: Theme | null, options: ThemePreferenceOptions = {}): void {
  const key = options.storageKey ?? DEFAULT_THEME_STORAGE_KEY;
  try {
    if (theme) window.localStorage.setItem(key, theme);
    else window.localStorage.removeItem(key);
  } catch { /* Storage may be disabled; cookie persistence is independent. */ }
  const domain = cookieDomain(window, options.cookieDomain);
  if (domain) try {
    // An explicit system choice must also override stale storage on a sibling origin.
    window.document.cookie = `${encodeURIComponent(key)}=${theme ?? "system"}; Domain=${domain}; Path=/; Max-Age=31536000; SameSite=Lax${window.location.protocol === "https:" ? "; Secure" : ""}`;
  } catch { /* Keep the current document usable when cookies are blocked too. */ }
}

export function readThemePreference(window: Window, options: ThemePreferenceOptions = {}): Theme | null {
  const key = options.storageKey ?? DEFAULT_THEME_STORAGE_KEY;
  if (cookieDomain(window, options.cookieDomain)) try {
    const prefix = `${encodeURIComponent(key)}=`;
    const cookie = window.document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith(prefix))?.slice(prefix.length);
    if (isTheme(cookie) || cookie === "system") return isTheme(cookie) ? cookie : null;
  } catch { /* Fall back to the original site's storage policy. */ }
  let saved: string | null = null;
  try { saved = window.localStorage.getItem(key); } catch { /* Storage may be disabled. */ }
  if (!isTheme(saved)) return null;
  // Migrate existing choices only; a system default is never persisted as a manual choice.
  if (cookieDomain(window, options.cookieDomain)) writeThemePreference(window, saved, options);
  return saved;
}

export function systemTheme(window: Window): Theme {
  if (typeof window.matchMedia === "function") {
    if (window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
    if (window.matchMedia("(prefers-color-scheme: light)").matches) return "light";
  }
  return "dark";
}

/** Shared bootstrap for the blocking Hugo head script and the platform entry point. */
export function initializeTheme(options: ThemePreferenceOptions = {}, root = document.documentElement): Theme {
  const window = root.ownerDocument.defaultView!;
  const theme = readThemePreference(window, options) ?? systemTheme(window);
  root.dataset.theme = theme;
  return theme;
}
