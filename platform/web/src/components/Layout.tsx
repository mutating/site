import { useLayoutEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useI18n, type MessageKey } from "../lib/i18n";
import { useSession } from "../lib/session";
import { api, apiURL, json } from "../lib/api";
import { ErrorBox, Icon } from "./UI";
import { Header as SharedHeader } from "@mutating/ui/react";
import { useData } from "../lib/useData";
import type { GitHubStatus } from "../lib/github";

const siteOrigin = import.meta.env.VITE_SITE_ORIGIN || "https://mutating.tech";

export function Header() {
  const { t, language, setLanguage } = useI18n();
  const location = useLocation();
  const languageURL = (code: string) => {
    const query = new URLSearchParams(location.search);
    query.set("tmc-language-choice", code);
    return `${location.pathname}?${query}${location.hash}`;
  };
  const headerRef = useRef<HTMLElement>(null);
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
    />
  );
}
export function Avatar({ large = false }: { large?: boolean }) {
  const { user } = useSession();
  return (
    <span className={`avatar ${large ? "large" : ""}`}>
      {user?.avatar_version ? (
        <img
          src={apiURL(`users/me/avatar?v=${user.avatar_version}`)}
          alt=""
        />
      ) : (
        (user?.first_name || user?.nickname || user?.email || "?")
          .slice(0, 1)
          .toUpperCase()
      )}
    </span>
  );
}
export function Layout() {
  const { t } = useI18n();
  const { user, setUser } = useSession();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
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
  const accountActions = (
    <>
      <Link className="account-action account-profile" to="/profile">
        <Icon name="user" size={14} />
        {t("profile")}
      </Link>
      <button
        className="account-action logout-button"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await api("auth/session", json("DELETE"));
            setUser(null);
          } catch (e) {
            setError(e);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Icon name="exit" size={14} />
        {t("logout")}
      </button>
    </>
  );
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
          <div className="sidebar-account">
            <div className="account-card">
              <div className="account-identity">
                <Avatar />
                <span>
                  <strong>
                    {user?.first_name || user?.nickname || user?.email}
                  </strong>
                  <small>{user?.email}</small>
                </span>
              </div>
              <div className="account-actions">{accountActions}</div>
            </div>
            <div className="mobile-account-actions">{accountActions}</div>
            <ErrorBox error={error} />
          </div>
        </aside>
        <main id="main" className="main-content">
          <Outlet />
        </main>
      </div>
    </>
  );
}
