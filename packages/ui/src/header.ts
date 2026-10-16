import { mountBrand, type BrandOptions } from "./brand";
import { createScope, type Cleanup } from "./internal";
import { createThemeController, mountThemeToggle, type ThemeController, type ThemeOptions } from "./theme";

export interface HeaderOptions {
  /** An externally owned controller is never destroyed by the header. */
  theme?: ThemeController;
  themeOptions?: ThemeOptions;
  brand?: BrandOptions;
  languageStorageKey?: string;
}

export function mountLanguageSwitcher(switcher: HTMLDetailsElement, storageKey = "tmc-language"): Cleanup {
  const scope = createScope(switcher);
  const { window, document, signal } = scope;
  const summary = switcher.querySelector<HTMLElement>(":scope > summary");
  const hover = window.matchMedia("(min-width: 1041px) and (hover: hover) and (pointer: fine)");
  const desktop = window.matchMedia("(min-width: 1041px)");
  const isDesktop = switcher.classList.contains("tmc-language-switcher--desktop");
  const close = () => { switcher.open = false; };
  if (isDesktop) {
    switcher.addEventListener("pointerenter", () => { if (hover.matches) switcher.open = true; }, { signal });
    switcher.addEventListener("pointerleave", () => { if (hover.matches) close(); }, { signal });
    summary?.addEventListener("click", (event) => {
      if (hover.matches && event.detail !== 0) { event.preventDefault(); switcher.open = true; }
    }, { signal });
  }
  switcher.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && switcher.open) { event.stopPropagation(); close(); summary?.focus(); }
  }, { signal });
  switcher.querySelectorAll<HTMLElement>("[data-language-option]").forEach((option) => {
    option.addEventListener("click", () => {
      try { window.localStorage.setItem(storageKey, option.dataset.languageOption!); } catch { /* Optional persistence. */ }
      close();
    }, { signal });
  });
  document.addEventListener("pointerdown", (event) => {
    if (event.target instanceof window.Node && !switcher.contains(event.target)) close();
  }, { signal });
  const syncLayout = () => {
    const isMobile = switcher.classList.contains("tmc-language-switcher--mobile");
    if ((desktop.matches && isMobile) || (!desktop.matches && isDesktop)) close();
  };
  desktop.addEventListener("change", syncLayout, { signal });
  scope.add(close);
  syncLayout();
  return scope.destroy;
}

export function mountMobileMenu(menu: HTMLDetailsElement): Cleanup {
  const scope = createScope(menu);
  const { window, document, signal } = scope;
  const summary = menu.querySelector<HTMLElement>(":scope > summary");
  const close = () => {
    menu.open = false;
    menu.querySelectorAll<HTMLDetailsElement>("details[open]").forEach((child) => { child.open = false; });
  };
  menu.querySelectorAll("a").forEach((link) => link.addEventListener("click", close, { signal }));
  document.addEventListener("pointerdown", (event) => {
    if (event.target instanceof window.Node && !menu.contains(event.target)) close();
  }, { signal });
  menu.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.defaultPrevented && menu.open) { close(); summary?.focus(); }
  }, { signal });
  const desktop = window.matchMedia("(min-width: 1121px)");
  desktop.addEventListener("change", () => { if (desktop.matches) close(); }, { signal });
  scope.add(close);
  return scope.destroy;
}

/** Mount server-rendered markup. React uses the individual behaviors instead. */
export function mountHeader(header: HTMLElement, options: HeaderOptions = {}): Cleanup {
  const scope = createScope(header);
  const theme = options.theme ?? createThemeController({ root: header.ownerDocument.documentElement, ...options.themeOptions });
  if (!options.theme) scope.add(theme.destroy);
  header.querySelectorAll<HTMLElement>(".tmc-brand").forEach((brand) => scope.add(mountBrand(brand, options.brand)));
  header.querySelectorAll<HTMLButtonElement>(".tmc-theme-toggle").forEach((button) => scope.add(mountThemeToggle(button, theme)));
  header.querySelectorAll<HTMLDetailsElement>(".tmc-language-switcher").forEach((switcher) => scope.add(mountLanguageSwitcher(switcher, options.languageStorageKey ?? switcher.dataset.tmcLanguageStorageKey)));
  header.querySelectorAll<HTMLDetailsElement>(".tmc-mobile-menu").forEach((menu) => scope.add(mountMobileMenu(menu)));
  return scope.destroy;
}
