import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useI18n, type MessageKey } from "../lib/i18n";
import { useSession } from "../lib/session";
import { api, json } from "../lib/api";
import { ErrorBox, Icon } from "./UI";
import { Brand } from "./Brand";

const siteOrigin = import.meta.env.VITE_SITE_ORIGIN || "https://mutating.tech";

export function Header() {
  const { t, language, setLanguage } = useI18n();
  const [theme, setTheme] = useState(document.documentElement.dataset.theme);
  return (
    <header className="site-header">
      <Brand href={`${siteOrigin}${language === "ru" ? "/ru/" : "/"}`} />
      <span className="header-caption">{t("account")}</span>
      <div className="header-actions">
        <button
          className="language-button"
          aria-label={t("language")}
          onClick={() => setLanguage(language === "ru" ? "en" : "ru")}
        >
          {language.toUpperCase()}
        </button>
        <button
          className="theme-toggle"
          aria-label={t("theme")}
          onClick={() => {
            const next = theme === "dark" ? "light" : "dark";
            setTheme(next);
            document.documentElement.dataset.theme = next;
            try {
              localStorage.setItem("tmc-theme", next);
            } catch {}
          }}
        >
          <span className={theme === "dark" ? "selected" : ""}>☾</span>
          <span className={theme === "light" ? "selected" : ""}>☀</span>
        </button>
      </div>
    </header>
  );
}
export function Avatar({ large = false }: { large?: boolean }) {
  const { user } = useSession();
  return (
    <span className={`avatar ${large ? "large" : ""}`}>
      {user?.avatar_version ? (
        <img
          src={`/account/api/users/me/avatar?v=${user.avatar_version}`}
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
  const { t, language } = useI18n();
  const { user, setUser } = useSession();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const links: [string, MessageKey | "Power Cells", string][] = [
    ["/", "home", "grid"],
    ["/power-cells", "Power Cells", "cells"],
    ["/cloud", "cloud", "cloud"],
    ["/organizations", "organizations", "users"],
    ["/profile", "profile", "user"],
  ];
  return (
    <>
      <a className="skip-link" href="#main">
        {t("skip")}
      </a>
      <Header />
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
            <Link className="account-link" to="/profile">
              <Avatar />
              <span>
                <strong>
                  {user?.first_name || user?.nickname || user?.email}
                </strong>
                <small>{user?.email}</small>
              </span>
            </Link>
            <button
              className="logout-button"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError(null);
                try {
                  await api("session", json("DELETE"));
                  setUser(null);
                } catch (e) {
                  setError(e);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <Icon name="exit" size={17} />
              {t("logout")}
            </button>
            <ErrorBox error={error} />
          </div>
        </aside>
        <main id="main" className="main-content">
          <Outlet />
          <footer className="page-footer">
            <a href={`${siteOrigin}${language === "ru" ? "/ru/" : "/"}`}>
              {t("website")} ↗
            </a>
          </footer>
        </main>
      </div>
    </>
  );
}
