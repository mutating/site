import { useLayoutEffect, useRef, useState } from "react";
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
      const style = getComputedStyle(header);
      const top = Number.parseFloat(style.top) || 0;
      const clearance = style.position === "sticky" ? Math.ceil(top + header.getBoundingClientRect().height + 16) : 0;
      root.style.setProperty(key, `${Math.max(110, clearance)}px`);
    };
    const observer = new ResizeObserver(update);
    observer.observe(header);
    const layoutObserver = new MutationObserver(update);
    layoutObserver.observe(header, { attributes: true, attributeFilter: ["data-tmc-too-tall"] });
    window.addEventListener("resize", update);
    update();
    return () => {
      observer.disconnect();
      layoutObserver.disconnect();
      window.removeEventListener("resize", update);
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
  const { t, language } = useI18n();
  const { user } = useSession();
  const github = useData(
    (signal) => api<GitHubStatus>("github/status", { signal }),
    user!.id,
  );
  const navRef = useRef<HTMLElement>(null);
  const [navOverflow, setNavOverflow] = useState({
    scrollable: false,
    atEnd: false,
  });
  useLayoutEffect(() => {
    const nav = navRef.current!;
    const view = nav.ownerDocument.defaultView!;
    let active = true;
    let frame = 0;
    const sync = () => {
      frame = 0;
      const limit = nav.scrollWidth - nav.clientWidth;
      const scrollable = limit > 1;
      const atEnd = !scrollable || nav.scrollLeft >= limit - 1;
      setNavOverflow(current =>
        current.scrollable === scrollable && current.atEnd === atEnd
          ? current
          : { scrollable, atEnd },
      );
    };
    const schedule = () => {
      if (!frame) frame = view.requestAnimationFrame(sync);
    };
    const resize = new view.ResizeObserver(schedule);
    const observeLinks = () => {
      for (const link of Array.from(nav.children)) resize.observe(link);
    };
    resize.observe(nav);
    observeLinks();
    const mutations = new view.MutationObserver(() => {
      observeLinks();
      schedule();
    });
    mutations.observe(nav, { childList: true });
    nav.addEventListener("scroll", schedule, { passive: true });
    view.addEventListener("resize", schedule);
    void nav.ownerDocument.fonts?.ready.then(() => {
      if (active) schedule();
    });
    sync();
    return () => {
      active = false;
      view.cancelAnimationFrame(frame);
      resize.disconnect();
      mutations.disconnect();
      nav.removeEventListener("scroll", schedule);
      view.removeEventListener("resize", schedule);
    };
  }, [language]);
  useLayoutEffect(() => {
    const nav = navRef.current!;
    const onWheel = (event: WheelEvent) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey) return;
      // Leave horizontal gestures and shift-wheel to the native scroller.
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      const limit = nav.scrollWidth - nav.clientWidth;
      if (limit <= 0) return;
      const unit =
        event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? nav.clientWidth
            : 1;
      const next = Math.max(
        0,
        Math.min(limit, nav.scrollLeft + event.deltaY * unit),
      );
      if (next === nav.scrollLeft) return;
      event.preventDefault();
      nav.scrollLeft = next;
    };
    nav.addEventListener("wheel", onWheel, { passive: false });
    return () => nav.removeEventListener("wheel", onWheel);
  }, []);
  const links: [string, MessageKey, string][] = [
    ["/", "home", "grid"],
    ["/compute", "compute", "compute"],
    ["/cloud", "cloud", "cloud"],
    ["/organizations", "organizations", "users"],
  ];
  if (github.data?.enabled) links.splice(2, 0, ["/actions", "actions", "play"]);
  return (
    <>
      <div className="app-shell">
        <aside className="sidebar">
          <p className="eyebrow sidebar-label">{t("account")}</p>
          <div
            className="sidebar-nav"
            data-scrollable={navOverflow.scrollable ? "true" : "false"}
            data-at-end={navOverflow.atEnd ? "true" : "false"}
          >
            <nav aria-label={t("navigation")} ref={navRef}>
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
                  <span>{t(label)}</span>
                  {label === "cloud" && <span className="soon-dot" />}
                </NavLink>
              ))}
            </nav>
            <span className="nav-scroll-hint" aria-hidden="true" />
          </div>
        </aside>
        <main id="main" className="main-content">
          <Outlet />
        </main>
      </div>
    </>
  );
}
