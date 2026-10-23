import { mountBrand, type BrandOptions } from "./brand";
import { createScope, type Cleanup } from "./internal";
import { mountDropdown } from "./dropdown";
import { createThemeController, mountThemeToggle, type ThemeController, type ThemeOptions, type ThemeToggleOptions } from "./theme";

export interface HeaderOptions {
  /** An externally owned controller is never destroyed by the header. */
  theme?: ThemeController;
  themeOptions?: ThemeOptions;
  /** False defers brand setup to a separately owned mountBrand call. */
  brand?: BrandOptions | false;
  languageStorageKey?: string;
  language?: LanguageSwitcherOptions;
  /** False defers menu setup to a separately owned mountMobileMenu call. */
  mobileMenu?: MobileMenuOptions | false;
  themeToggle?: ThemeToggleOptions;
}

export interface LanguageSwitcherOptions {
  /** Original HTML behavior uses 1041; the React adapter follows its CSS at 1121. */
  desktopMinWidth?: number;
  /** Defaults to true. Disable to preserve the originating list on new-tab selection. */
  closeOnSelection?: boolean;
  /** Defaults to true. Disable when the enclosing document also observes Escape. */
  stopEscapePropagation?: boolean;
}

export interface MobileMenuOptions {
  /** Delegate link clicks when a renderer can replace anchors after mounting. */
  delegateLinks?: boolean;
  /** All closing behaviors default to true. */
  closeOnEscape?: boolean;
  closeOnDesktop?: boolean;
  closeNestedMenus?: boolean;
}

export function mountLanguageSwitcher(switcher: HTMLDetailsElement, storageKey = "tmc-language", options: LanguageSwitcherOptions = {}): Cleanup {
  const scope = createScope(switcher);
  const { window, document, on } = scope;
  const summary = switcher.querySelector<HTMLElement>(":scope > summary");
  const minWidth = options.desktopMinWidth ?? 1041;
  const hover = window.matchMedia(`(min-width: ${minWidth}px) and (hover: hover) and (pointer: fine)`);
  const desktop = window.matchMedia(`(min-width: ${minWidth}px)`);
  const isDesktop = switcher.classList.contains("tmc-language-switcher--desktop");
  const close = () => { switcher.open = false; };
  if (isDesktop) {
    on(switcher, "pointerenter", () => { if (hover.matches) switcher.open = true; });
    on(switcher, "pointerleave", () => { if (hover.matches) close(); });
    if (summary) on<MouseEvent>(summary, "click", (event) => {
      if (hover.matches && event.detail !== 0) { event.preventDefault(); switcher.open = true; }
    });
  }
  on<KeyboardEvent>(switcher, "keydown", (event) => {
    if (event.key === "Escape" && switcher.open) {
      if (options.stopEscapePropagation !== false) event.stopPropagation();
      close();
      summary?.focus();
    }
  });
  switcher.querySelectorAll<HTMLElement>("[data-language-option]").forEach((option) => {
    on(option, "click", () => {
      try { window.localStorage.setItem(storageKey, option.dataset.languageOption!); } catch { /* Optional persistence. */ }
      if (options.closeOnSelection !== false) close();
    });
  });
  on(document, "pointerdown", (event) => {
    if (event.target instanceof window.Node && !switcher.contains(event.target)) close();
  });
  const syncLayout = () => {
    const isMobile = switcher.classList.contains("tmc-language-switcher--mobile");
    if ((desktop.matches && isMobile) || (!desktop.matches && isDesktop)) close();
  };
  on(desktop, "change", syncLayout);
  scope.add(close);
  syncLayout();
  return scope.destroy;
}

export function mountMobileMenu(menu: HTMLDetailsElement, options: MobileMenuOptions = {}): Cleanup {
  return mountDropdown(menu, { ...options, closeOnDesktop: options.closeOnDesktop !== false });
}

/** Mount server-rendered markup. React uses the individual behaviors instead. */
export function mountHeader(header: HTMLElement, options: HeaderOptions = {}): Cleanup {
  const scope = createScope(header);
  const theme = options.theme ?? createThemeController({ root: header.ownerDocument.documentElement, ...options.themeOptions });
  if (!options.theme) scope.add(theme.destroy);
  // Preserve the site's initialization order: animation failures must not
  // prevent the theme and language controls from becoming usable.
  header.querySelectorAll<HTMLButtonElement>(".tmc-theme-toggle").forEach((button) => scope.add(mountThemeToggle(button, theme, options.themeToggle)));
  header.querySelectorAll<HTMLDetailsElement>(".tmc-language-switcher").forEach((switcher) => scope.add(mountLanguageSwitcher(switcher, options.languageStorageKey ?? switcher.dataset.tmcLanguageStorageKey, options.language)));
  const brandOptions = options.brand;
  if (brandOptions !== false) header.querySelectorAll<HTMLElement>(".tmc-brand").forEach((brand) => scope.add(mountBrand(brand, brandOptions)));
  const menuOptions = options.mobileMenu;
  if (menuOptions !== false) header.querySelectorAll<HTMLDetailsElement>(".tmc-mobile-menu").forEach((menu) => scope.add(mountMobileMenu(menu, menuOptions)));
  return scope.destroy;
}
