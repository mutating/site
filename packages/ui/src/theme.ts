import { createScope, type Cleanup } from "./internal";

export type Theme = "light" | "dark";
export const THEME_CHANGE_EVENT = "tmc-theme-change";
export const THEME_TOGGLE_EVENT = "tmc-theme-toggle-blink";
export const DEFAULT_THEME_STORAGE_KEY = "tmc-theme";

export interface ThemeOptions {
  /** Defaults to document.documentElement. */
  root?: HTMLElement;
  storageKey?: string;
  /** Without an explicit theme, prefer saved choice, then system preference. */
  initialTheme?: Theme;
  /** Defaults to true; false keeps each document's theme until reload/system change. */
  syncAcrossTabs?: boolean;
  /** Storage rechecks saved choice on each system change, including denied/cleared storage. */
  preferenceSource?: "memory" | "storage";
}

export interface ThemeController {
  readonly root: HTMLElement;
  getTheme(): Theme;
  setTheme(theme: Theme): void;
  toggle(): void;
  useSystemTheme(): void;
  subscribe(listener: (theme: Theme) => void): Cleanup;
  destroy(): void;
}

export interface ThemeToggleOptions {
  /** Defaults to true; false retains a plain action button's existing semantics. */
  exposePressedState?: boolean;
}

export function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark";
}

/** Call explicitly before rendering when avoiding a theme flash is important. */
export function createThemeController(options: ThemeOptions = {}): ThemeController {
  const root = options.root ?? document.documentElement;
  const scope = createScope(root);
  const { window, on } = scope;
  const dark = window.matchMedia("(prefers-color-scheme: dark)");
  const light = window.matchMedia("(prefers-color-scheme: light)");
  const key = options.storageKey ?? DEFAULT_THEME_STORAGE_KEY;
  const listeners = new Set<(theme: Theme) => void>();
  let saved: string | null = null;
  try { saved = window.localStorage.getItem(key); } catch { /* Storage can be disabled. */ }
  // Keep the preference in memory too: storage denial must not reset a choice.
  let preference: Theme | null = options.initialTheme ?? (isTheme(saved) ? saved : null);
  const systemTheme = (): Theme => dark.matches ? "dark" : light.matches ? "light" : "dark";
  let theme = preference ?? systemTheme();
  const publish = (next: Theme) => {
    if (scope.disposed) return;
    root.dataset.theme = next;
    if (theme === next) return;
    theme = next;
    listeners.forEach((listener) => listener(theme));
    root.dispatchEvent(new window.CustomEvent(THEME_CHANGE_EVENT, { detail: { theme }, bubbles: true }));
  };
  root.dataset.theme = theme;
  const syncSystem = () => {
    if (options.preferenceSource === "storage") {
      let stored: string | null = null;
      try { stored = window.localStorage.getItem(key); } catch { /* Follow the system without persistence. */ }
      preference = isTheme(stored) ? stored : null;
    }
    if (!preference) publish(systemTheme());
  };
  on(dark, "change", syncSystem);
  on(light, "change", syncSystem);
  on<StorageEvent>(window, "storage", (event) => {
    if (options.syncAcrossTabs === false) return;
    if (event.key !== key && event.key !== null) return;
    if (event.storageArea) {
      try { if (event.storageArea !== window.localStorage) return; } catch { return; }
    }
    preference = isTheme(event.newValue) ? event.newValue : null;
    publish(preference ?? systemTheme());
  });
  const setTheme = (next: Theme) => {
    if (scope.disposed) return;
    preference = next;
    try { window.localStorage.setItem(key, next); } catch { /* Keep in-memory choice. */ }
    publish(next);
  };
  scope.add(() => listeners.clear());
  return {
    root,
    getTheme: () => theme,
    setTheme,
    toggle() {
      if (scope.disposed) return;
      setTheme(theme === "dark" ? "light" : "dark");
      window.dispatchEvent(new window.Event(THEME_TOGGLE_EVENT));
    },
    useSystemTheme() {
      if (scope.disposed) return;
      preference = null;
      try { window.localStorage.removeItem(key); } catch { /* Storage can be disabled. */ }
      publish(systemTheme());
    },
    subscribe(listener) {
      if (scope.disposed) return () => {};
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    destroy: scope.destroy,
  };
}

export function mountThemeToggle(button: HTMLButtonElement, controller: ThemeController, options: ThemeToggleOptions = {}): Cleanup {
  const scope = createScope(button);
  const update = (theme: Theme) => button.setAttribute("aria-pressed", String(theme === "dark"));
  if (options.exposePressedState !== false) {
    update(controller.getTheme());
    scope.add(controller.subscribe(update));
  } else {
    button.removeAttribute("aria-pressed");
  }
  scope.on(button, "click", () => controller.toggle());
  return scope.destroy;
}
