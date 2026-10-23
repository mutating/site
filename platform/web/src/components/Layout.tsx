import { useLayoutEffect, useRef } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useI18n, type MessageKey } from "../lib/i18n";
import { useSession } from "../lib/session";
import { api } from "../lib/api";
import { Icon } from "./UI";
import { Header as SharedHeader, SquareButton } from "@mutating/ui/react";
import { useData } from "../lib/useData";
import type { GitHubStatus } from "../lib/github";
import { UserMenu } from "./UserMenu";
import { useHeaderPresence } from "../lib/useHeaderPresence";

const siteOrigin = import.meta.env.VITE_SITE_ORIGIN || "https://mutating.tech";

export function Header() {
  const { user, loading, error } = useSession();
  const { t, language, setLanguage } = useI18n();
  const location = useLocation();
  const languageURL = (code: string) => {
    const query = new URLSearchParams(location.search);
    query.set("tmc-language-choice", code);
    return `${location.pathname}?${query}${location.hash}`;
  };
  const headerRef = useRef<HTMLElement>(null);
  const hasUserMenu = useHeaderPresence(headerRef, !loading && !error && !!user);
  useLayoutEffect(() => {
    const header = headerRef.current!;
    const root = document.documentElement;
    const key = "--platform-header-clearance";
    const previous = root.style.getPropertyValue(key);
    const update = () => {
      const top = Number.parseFloat(getComputedStyle(header).top) || 0;
      root.style.setProperty(key, `${Math.max(110, Math.ceil(top + header.getBoundingClientRect().height + 16))}px`);
    };
    const observer = new ResizeObserver(update);
    observer.observe(header);
    update();
    return () => {
      observer.disconnect();
      if (previous) root.style.setProperty(key, previous);
      else root.style.removeProperty(key);
    };
  }, []);
  return (
    <SharedHeader
      ref={headerRef}
      className="site-header"
      brand={{
        href: `${siteOrigin}${language === "ru" ? "/ru/" : "/"}`,
        animation: {
          scrollTone: false,
          expandOnFocus: true,
          fitWordmarkToHeader: true,
          reactiveGaze: true,
        },
      }}
      themeLabel={t("theme")}
      language={{
        current: language,
        label: t("language"),
        groupLabel: language === "ru" ? "Языки" : "Languages",
        options: ["en", "ru"].map(code => ({ code, href: languageURL(code) })),
      }}
      onLanguageChange={code => { if (code === "ru" || code === "en") setLanguage(code); }}
      menuLabel={language === "ru" ? "Открыть меню навигации" : "Open navigation menu"}
      menuText={language === "ru" ? "Меню" : "Menu"}
      mobileNavigationLabel={language === "ru" ? "Мобильная навигация" : "Mobile navigation"}
      adjacent={hasUserMenu ? !loading && !error && user ? <UserMenu key={user.id} />
        : <SquareButton className="user-menu-placeholder" disabled tabIndex={-1} aria-hidden="true" aria-label="">{null}</SquareButton>
        : undefined}
    />
  );
}
export function Layout() {
  const { t } = useI18n();
  const { user } = useSession();
  const github = useData(
    (signal) => api<GitHubStatus>("github/status", { signal }),
    user!.id,
  );
  const links: [string, MessageKey | "Power Cells", string][] = [
    ["/", "home", "grid"],
    ["/power-cells", "Power Cells", "cells"],
    ["/cloud", "cloud", "cloud"],
    ["/organizations", "organizations", "users"],
  ];
  if (github.data?.enabled) links.splice(2, 0, ["/actions", "actions", "play"]);
  return (
    <>
      <div className="app-shell">
        <aside className="sidebar">
          <p className="eyebrow sidebar-label">{t("account")}</p>
          <nav aria-label={t("navigation")}>
            {links.map(([path, label, icon]) => (
              <NavLink
                key={path}
                to={path}
                end={path === "/"}
                className={({ isActive }) =>
                  isActive ? "nav-link active" : "nav-link"
                }
              >
                <Icon name={icon} />
                <span>{label === "Power Cells" ? label : t(label)}</span>
                {label === "cloud" && <span className="soon-dot" />}
              </NavLink>
            ))}
          </nav>
        </aside>
        <main id="main" className="main-content">
          <Outlet />
        </main>
      </div>
    </>
  );
}
