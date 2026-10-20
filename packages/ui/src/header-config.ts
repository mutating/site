import type { BrandOptions } from "./brand";

export interface HeaderItem {
  id: string;
  label: string;
  href: string;
  icon?: "github";
  active?: boolean;
  target?: "_self" | "_blank";
  rel?: string;
}

export interface LanguageOption { code: string; href: string; label?: string }
export interface HeaderLanguage {
  current: string;
  options: readonly LanguageOption[];
  label: string;
  groupLabel: string;
  storageKey?: string;
  /** Reserve consistent width even when some languages lack the current page. */
  supportedCodes?: readonly string[];
}
export interface HeaderBrand {
  href: string;
  label?: string;
  animation?: BrandOptions;
}
export interface HeaderLink { href: string; label: string }

/** The same data contract is accepted by React and Hugo. */
export interface HeaderConfig {
  /** Inherit the embedding page's color, font family and line height, as the original site did. */
  inheritTypography?: boolean;
  items?: readonly HeaderItem[];
  brand?: HeaderBrand;
  language?: HeaderLanguage;
  themeLabel?: string;
  showThemeToggle?: boolean;
  navigationLabel?: string;
  mobileNavigationLabel?: string;
  menuLabel?: string;
  menuText?: string;
  adjacentLink?: HeaderLink;
}

/** An ordinary Header configuration, with the current home page's copy and URLs. */
export function createMutatingHomeHeader(language: "en" | "ru" = "en"): HeaderConfig {
  const ru = language === "ru";
  const home = ru ? "/ru/" : "/";
  return {
    inheritTypography: true,
    brand: {
      href: home,
      label: ru ? "Перезагрузить главную страницу The Mutating Company" : "Reload The Mutating Company home page",
      animation: { activation: "reload-home" },
    },
    items: [
      { id: "sources", label: ru ? "Исходники" : "Sources", href: "https://github.com/mutating", icon: "github" },
      { id: "news", label: ru ? "Новости" : "News", href: `${home}news/` },
      { id: "blog", label: ru ? "Блог" : "Blog", href: `${home}blog/` },
      { id: "contact", label: ru ? "Контакты" : "Contact", href: `${home}#contact` },
    ],
    language: {
      current: language,
      options: [
        { code: "en", href: "/?tmc-language-choice=en" },
        { code: "ru", href: "/ru/?tmc-language-choice=ru" },
      ],
      label: ru ? "Выбрать язык; текущий язык RU" : "Choose language; current language EN",
      groupLabel: ru ? "Языки" : "Languages",
    },
    navigationLabel: ru ? "Основная навигация" : "Main navigation",
    mobileNavigationLabel: ru ? "Мобильная навигация" : "Mobile navigation",
    themeLabel: ru ? "Переключить цветовую тему" : "Toggle color theme",
    menuLabel: ru ? "Открыть меню навигации" : "Open navigation menu",
    menuText: ru ? "Меню" : "Menu",
    adjacentLink: { href: "https://platform.mutating.tech/", label: ru ? "Открыть платформу" : "Open platform" },
  };
}
