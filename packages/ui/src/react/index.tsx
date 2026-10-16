import {
  createContext, useContext, useEffect, useRef, useState, useSyncExternalStore,
  type ComponentPropsWithoutRef, type CSSProperties, type ReactNode,
} from "react";
import { mountBrand, type BrandOptions } from "../brand";
import type { HeaderConfig, HeaderItem, LanguageOption } from "../header-config";
import { GITHUB_PATH } from "../icons";
export type { HeaderConfig, HeaderItem, HeaderBrand, HeaderLanguage, HeaderLink, LanguageOption } from "../header-config";
import { mountLanguageSwitcher, mountMobileMenu } from "../header";
import { createThemeController, mountThemeToggle, type Theme, type ThemeController, type ThemeOptions } from "../theme";

const classes = (...values: (string | undefined)[]) => values.filter(Boolean).join(" ");

export interface ButtonProps extends ComponentPropsWithoutRef<"button"> {
  variant?: "primary" | "secondary" | "dark";
  size?: "default" | "compact";
}
export function Button({ variant = "primary", size = "default", className, type = "button", ...props }: ButtonProps) {
  return <button {...props} type={type} className={classes("tmc-button", `tmc-button--${variant}`, size === "compact" ? "tmc-button--compact" : undefined, className)} />;
}

export interface BrandProps extends ComponentPropsWithoutRef<"a"> {
  href: string;
  animation?: BrandOptions;
}
export function Brand({ href, animation, className, "aria-label": label = "The Mutating Company", ...props }: BrandProps) {
  const ref = useRef<HTMLAnchorElement>(null);
  const { expandWordmark, scrollTone, blinkIntervalMs, activation } = animation ?? {};
  useEffect(() => mountBrand(ref.current!, { expandWordmark, scrollTone, blinkIntervalMs, activation }), [expandWordmark, scrollTone, blinkIntervalMs, activation]);
  return (
    <a {...props} ref={ref} href={href} aria-label={label} className={classes("tmc-brand", className)}>
      <span className="tmc-brand__mark tmc-brand__mark--eye" aria-hidden="true">
        <span className="tmc-eye"><span className="tmc-eye__clip">
          <span className="tmc-eye__outline" /><span className="tmc-eye__surface" /><span className="tmc-eye__pupil" />
        </span></span>
      </span>
      <span className="tmc-brand__wordmark-stage" data-can-expand="true" aria-hidden="true">
        <span className="tmc-brand__wordmark">
          <span className="tmc-brand__word-part tmc-brand__word-part--t"><span className="tmc-brand__word-anchor">T</span><span className="tmc-brand__word-reveal" data-text="he">he</span></span>
          <span className="tmc-brand__word-part tmc-brand__word-part--m"><span className="tmc-brand__word-anchor">M</span><span className="tmc-brand__word-reveal" data-text="utating">utating</span></span>
          <span className="tmc-brand__word-part"><span className="tmc-brand__word-anchor">C</span><span className="tmc-brand__word-reveal" data-text="ompany">ompany</span></span>
        </span>
      </span>
    </a>
  );
}

const ThemeContext = createContext<{ controller: ThemeController | null; initialTheme: Theme } | undefined>(undefined);
export interface ThemeProviderProps extends ThemeOptions { children: ReactNode }
export function ThemeProvider({ children, root, storageKey, initialTheme }: ThemeProviderProps) {
  const [controller, setController] = useState<ThemeController | null>(null);
  useEffect(() => {
    const next = createThemeController({ root, storageKey, initialTheme });
    setController(next);
    return next.destroy;
  }, [root, storageKey, initialTheme]);
  return <ThemeContext.Provider value={{ controller, initialTheme: initialTheme ?? "dark" }}>{children}</ThemeContext.Provider>;
}
const noopSubscribe = () => () => {};
export function useTheme() {
  const context = useContext(ThemeContext);
  if (context === undefined) throw new Error("useTheme and ThemeToggle require a ThemeProvider.");
  const { controller, initialTheme } = context;
  const serverTheme = () => initialTheme;
  const theme = useSyncExternalStore(
    controller?.subscribe ?? noopSubscribe,
    controller?.getTheme ?? serverTheme,
    serverTheme,
  );
  return { theme, controller };
}

/** aria-label should describe the fixed action, e.g. “Toggle color theme”. */
export function ThemeToggle({ className, type = "button", ...props }: ComponentPropsWithoutRef<"button">) {
  const ref = useRef<HTMLButtonElement>(null);
  const { theme, controller } = useTheme();
  useEffect(() => {
    if (controller) return mountThemeToggle(ref.current!, controller);
  }, [controller]);
  return (
    <button {...props} ref={ref} type={type} aria-label={props["aria-label"] ?? "Toggle color theme"} aria-pressed={theme === "dark"} className={classes("tmc-theme-toggle", className)}>
      <span className="tmc-theme-toggle__track" aria-hidden="true">
        <span className="tmc-theme-toggle__sun"><span className="tmc-theme-toggle__sun-core" /><span className="tmc-theme-toggle__sun-rays" /><span className="tmc-theme-toggle__sun-rays tmc-theme-toggle__sun-rays--diagonal" /></span>
        <span className="tmc-theme-toggle__moon" /><span className="tmc-theme-toggle__thumb" />
      </span>
    </button>
  );
}

export interface LanguageSwitcherProps {
  current: string;
  options: readonly LanguageOption[];
  label: string;
  groupLabel: string;
  variant?: "standalone" | "desktop" | "mobile";
  storageKey?: string;
}
export function LanguageSwitcher({ current, options, label, groupLabel, variant = "standalone", storageKey }: LanguageSwitcherProps) {
  const ref = useRef<HTMLDetailsElement>(null);
  // Option changes can replace DOM nodes, so remount their listeners too.
  const signature = JSON.stringify(options);
  useEffect(() => mountLanguageSwitcher(ref.current!, storageKey), [storageKey, variant, signature]);
  const width = Math.max(48, 24 + Math.max(current.length, ...options.map((option) => option.code.length)) * 7);
  return (
    <details ref={ref} className={`tmc-language-switcher tmc-language-switcher--${variant}`} style={{ "--tmc-language-switcher-desktop-width": `${width}px` } as CSSProperties}>
      <summary aria-label={label}><span>{current.toUpperCase()}</span></summary>
      <div className="tmc-language-switcher__list" role="group" aria-label={groupLabel}>
        {options.filter((option) => option.code !== current).map((option) => (
          <a key={option.code} className="tmc-language-switcher__option" href={option.href} lang={option.code} hrefLang={option.code} data-language-option={option.code}>{option.label ?? option.code.toUpperCase()}</a>
        ))}
      </div>
    </details>
  );
}

export interface HeaderProps extends HeaderConfig {
  className?: string;
  /** Supply a router Link while keeping the library's link content and attributes. */
  renderLink?: (item: HeaderItem, attributes: ComponentPropsWithoutRef<"a">, children: ReactNode) => ReactNode;
}

function MenuLink({ item, renderLink }: { item: HeaderItem; renderLink?: HeaderProps["renderLink"] }) {
  const attributes: ComponentPropsWithoutRef<"a"> = {
    href: item.href,
    className: item.icon ? "tmc-source-link" : undefined,
    "aria-current": item.active ? "page" : undefined,
    target: item.target,
    rel: item.rel ?? (item.target === "_blank" ? "noopener noreferrer" : undefined),
  };
  const children = item.icon === "github" ? <><span>{item.label}</span><svg className="tmc-source-link__icon" viewBox="0 0 16 16" aria-hidden="true"><path d={GITHUB_PATH} /></svg></> : item.label;
  return renderLink ? renderLink(item, attributes, children) : <a {...attributes}>{children}</a>;
}

export function Header({
  items = [], brand = { href: "/" }, language, themeLabel = "Toggle color theme", showThemeToggle = true,
  navigationLabel = "Navigation", mobileNavigationLabel = navigationLabel,
  menuLabel = "Open navigation menu", menuText = "Menu", adjacentLink, className, renderLink,
}: HeaderProps) {
  const menuRef = useRef<HTMLDetailsElement>(null);
  const hasMenu = items.length > 0 || !!language;
  const itemsSignature = JSON.stringify(items);
  useEffect(() => {
    if (menuRef.current) return mountMobileMenu(menuRef.current);
  }, [hasMenu, itemsSignature, language?.current]);
  const links = items.map((item) => <MenuLink key={item.id} item={item} renderLink={renderLink} />);
  return (
    <header className={classes("tmc-header-shell", className)}>
      <div className="tmc-header">
        <Brand href={brand.href} aria-label={brand.label} animation={brand.animation} />
        {items.length > 0 && <nav className="tmc-nav" aria-label={navigationLabel}>{links}</nav>}
        <div className="tmc-header-actions">
          {language && <LanguageSwitcher {...language} variant="desktop" />}
          {showThemeToggle && <ThemeToggle aria-label={themeLabel} />}
          {hasMenu && <details ref={menuRef} className="tmc-mobile-menu">
            <summary aria-label={menuLabel}>
              <span className="tmc-mobile-menu__label">{menuText}</span>
              <svg className="tmc-mobile-menu__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
            </summary>
            <nav aria-label={mobileNavigationLabel}>
              {language && <div className="tmc-mobile-menu__language"><LanguageSwitcher {...language} variant="mobile" /></div>}
              {items.length > 0 && <div className="tmc-mobile-menu__links">{links}</div>}
            </nav>
          </details>}
        </div>
      </div>
      {adjacentLink && <a className="tmc-platform-link" href={adjacentLink.href} aria-label={adjacentLink.label}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.05" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" /><path d="m10 17 5-5-5-5" /><path d="M15 12H3" />
        </svg>
      </a>}
    </header>
  );
}
