import { useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "@mutating/ui/react";
import {
  BrowserRouter,
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { I18nProvider, useI18n } from "./lib/i18n";
import { SessionProvider, useSession } from "./lib/session";
import { Header, Layout } from "./components/Layout";
import { Empty, ErrorBox, Loading } from "./components/UI";
import { Auth } from "./pages/Auth";
import { Overview } from "./pages/Overview";
import { PowerCells } from "./pages/PowerCells";
import { Computers } from "./pages/Computers";
import { Computer } from "./pages/Computer";
import { Organizations } from "./pages/Organizations";
import { Organization } from "./pages/Organization";
import { Invitation } from "./pages/Invitation";
import { Profile } from "./pages/Profile";
import { Cloud } from "./pages/Cloud";
import { Actions } from "./pages/Actions";
import { ActionRepository } from "./pages/ActionRepository";
import { ActionRepositoryHistory } from "./pages/ActionRepositoryHistory";
import { ApiTokens } from "./pages/ApiTokens";
import "@mutating/ui/fonts.css";
import "@mutating/ui/styles/brand.css";
import "@mutating/ui/styles/header.css";
import "./styles/brand.css";
import "./styles/app.css";
function Guard() {
  const { user } = useSession();
  const location = useLocation();
  return user ? (
    <Outlet />
  ) : (
    <Navigate
      to="/login"
      state={{ from: location.pathname + location.search }}
      replace
    />
  );
}
function NotFound() {
  const { t } = useI18n();
  return (
    <>
      <Empty title={t("notFound")}>{t("notFoundLead")}</Empty>
      <Link className="button secondary" to="/">
        {t("home")}
      </Link>
    </>
  );
}
function App() {
  const { user, loading, error, reload } = useSession();
  const { t } = useI18n();
  const location = useLocation();
  const interrupted = useRef(false);
  useEffect(() => {
    if (!location.hash) interrupted.current = false;
    const interrupt = () => { interrupted.current = true; };
    const events = ["pointerdown", "keydown", "wheel", "touchmove"] as const;
    for (const event of events) window.addEventListener(event, interrupt, { capture: true, passive: true });
    return () => {
      for (const event of events) window.removeEventListener(event, interrupt, true);
    };
  }, [location.key, location.hash]);
  const previousPath = useRef(location.pathname);
  useEffect(() => {
    const samePage = previousPath.current === location.pathname;
    previousPath.current = location.pathname;
    if (!samePage || !location.state?.keepScroll) window.scrollTo(0, 0);
  }, [location.pathname, location.search]);
  useEffect(() => {
    document.title = `${t("account")} — The Mutating Company`;
  }, [t("account")]);
  return (
    <>
      {!loading && !error && user && (
        <a className="skip-link" href="#main">
          {t("skip")}
        </a>
      )}
      {/* Keep active pointer presses and keyboard focus across session changes. */}
      <Header />
      {loading || error ? (
        <main className="boot-state">
          {loading ? <Loading /> : <ErrorBox error={error} retry={reload} />}
        </main>
      ) : (
        <Routes>
          <Route path="login" element={<Auth key="login" />} />
          <Route path="register" element={<Auth key="register" register />} />
          <Route element={<Guard />}>
            <Route element={<Layout />}>
              <Route index element={<Overview />} />
              <Route path="power-cells" element={<PowerCells />} />
              <Route path="power-cells/computers" element={<Computers />} />
              <Route path="power-cells/computers/:computerId" element={<Computer interrupted={interrupted} />} />
              <Route path="organizations" element={<Organizations />} />
              <Route path="organizations/:companyId" element={<Organization />} />
              <Route path="invitations/:token" element={<Invitation />} />
              <Route path="profile" element={<Profile />} />
              <Route path="api-tokens" element={<ApiTokens key={user?.id} />} />
              <Route path="cloud" element={<Cloud />} />
              <Route path="actions" element={<Actions key={user?.id} />} />
              <Route path="actions/repositories/:repositoryId" element={<ActionRepository />} />
              <Route path="actions/repositories/:repositoryId/history" element={<ActionRepositoryHistory />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Route>
        </Routes>
      )}
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <I18nProvider>
    <ThemeProvider
      initialTheme={document.documentElement.dataset.theme === "light" ? "light" : "dark"}
      syncAcrossTabs={false}
      preferenceSource="storage"
      cookieDomain="mutating.tech"
    >
      <SessionProvider>
        <BrowserRouter useTransitions={false}>
          <App />
        </BrowserRouter>
      </SessionProvider>
    </ThemeProvider>
  </I18nProvider>,
);
