import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
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
  useNavigationType,
  type Location,
} from "react-router-dom";
import { I18nProvider, useI18n } from "./lib/i18n";
import type { ConnectedPoolOrigin } from "./lib/compute";
import { SessionProvider, useSession } from "./lib/session";
import { Header, Layout } from "./components/Layout";
import { Empty, ErrorBox, Loading } from "./components/UI";
import { Auth } from "./pages/Auth";
import { Overview } from "./pages/Overview";
import { Compute } from "./pages/Compute";
import { ComputeComputers, connectedListEntry } from "./pages/ConnectedComputers";
import { ComputePool, ComputePools } from "./pages/ComputePools";
import { PersonalPool } from "./pages/PersonalPool";
import { Computer } from "./pages/Computer";
import { Organizations } from "./pages/Organizations";
import { Organization } from "./pages/Organization";
import { Invitation } from "./pages/Invitation";
import { Profile } from "./pages/Profile";
import { Cloud } from "./pages/Cloud";
import { Actions } from "./pages/Actions";
import { ActionRepository } from "./pages/ActionRepository";
import { ActionRepositoryHistory } from "./pages/ActionRepositoryHistory";
import "@mutating/ui/fonts.css";
import "@mutating/ui/styles/brand.css";
import "@mutating/ui/styles/header.css";
import "./styles/brand.css";
import "./styles/app.css";
import "./styles/compute.css";
declare global {
  interface Window {
    __TMC_INITIAL_POOL_LOCATION__?: { key: string; origin: ConnectedPoolOrigin };
  }
}
const initialPoolLocation = window.__TMC_INITIAL_POOL_LOCATION__;
delete window.__TMC_INITIAL_POOL_LOCATION__;

function connectedPoolReturn(
  location: Location,
  action: ReturnType<typeof useNavigationType>,
  previousPoolOrigin?: ConnectedPoolOrigin,
): ConnectedPoolOrigin | null {
  const origin = action === "REPLACE" ? location.state?.connectedPoolCorrection : action === "POP" ? previousPoolOrigin : null;
  const entry = connectedListEntry(location);
  return entry && origin?.entry === entry && typeof origin.userId === "string" && typeof origin.computerId === "string"
    ? origin : null;
}

function Guard({ pendingPoolReturn }: { pendingPoolReturn: ConnectedPoolOrigin | null }) {
  const { user } = useSession();
  const location = useLocation();
  return user ? (
    <Outlet key={user.id} />
  ) : (
    <Navigate
      to="/login"
      state={{
        from: location.pathname + location.search + location.hash,
        requiresSignIn: true,
        connectedPoolOrigin: location.state?.connectedPoolOrigin,
        connectedPoolListEntry: connectedListEntry(location),
        connectedPoolCorrection: pendingPoolReturn ?? undefined,
      }}
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
function LegacyCompute() {
  const location = useLocation();
  const user = useSession().user!;
  const path = location.pathname.replace(/^\/power-cells/, "") || "";
  const params = new URLSearchParams(location.search);
  let search = location.search;
  const legacyInventory = path === "/computers" || path === "/computers/";
  if (legacyInventory && !params.has("user_id") && !params.has("company_id")) {
    params.set("user_id", user.id);
    search = `?${params}`;
  }
  return (
    <Navigate
      to={{ pathname: `/compute${path}`, search, hash: location.hash }}
      replace
    />
  );
}
function LegacyApiTokens() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  params.set("section", "api-tokens");
  return <Navigate to={{ pathname: "/profile", search: `?${params}`, hash: location.hash }} replace />;
}
function App() {
  const { user, loading, error, reload } = useSession();
  const memberSearchDrafts = useMemo(() => new Map<string, string>(), [user?.id]);
  const { t } = useI18n();
  const location = useLocation();
  const action = useNavigationType();
  const [navigation, setNavigation] = useState(() => ({
    current: location,
    pendingPoolReturn: connectedPoolReturn(location, action,
      initialPoolLocation?.key !== location.key ? initialPoolLocation?.origin : undefined),
  }));
  if (navigation.current.key !== location.key) {
    setNavigation({
      current: location,
      pendingPoolReturn: connectedPoolReturn(location, action, navigation.current.state?.connectedPoolOrigin),
    });
  }
  const finishPoolReturn = useCallback(() => {
    setNavigation((current) => current.current.key === location.key && current.pendingPoolReturn
      ? { ...current, pendingPoolReturn: null } : current);
  }, [location.key]);
  useLayoutEffect(() => {
    if (!loading && !error && user && navigation.pendingPoolReturn && navigation.pendingPoolReturn.userId !== user.id) finishPoolReturn();
  }, [loading, error, user?.id, navigation.pendingPoolReturn, finishPoolReturn]);
  useLayoutEffect(() => {
    const main = document.getElementById("main");
    if (location.hash !== "#main" && document.activeElement === main) main?.blur();
  }, [location.pathname, location.hash]);
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
        <Link className="skip-link" to={{ search: location.search, hash: "#main" }}
          state={{ ...location.state, keepScroll: true }} onClick={(event) => {
            if (event.defaultPrevented || event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
            const main = document.getElementById("main");
            main?.focus({ preventScroll: true });
            main?.scrollIntoView({ block: "start" });
            if (location.hash === "#main") event.preventDefault();
          }}>
          {t("skip")}
        </Link>
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
          <Route element={<Guard pendingPoolReturn={navigation.pendingPoolReturn} />}>
            <Route element={<Layout />}>
              <Route index element={<Overview />} />
              <Route path="compute" element={<Compute />} />
              <Route path="compute/pools" element={<ComputePools />} />
              <Route path="compute/pools/personal" element={<PersonalPool />} />
              <Route path="compute/pools/:poolId" element={<ComputePool />} />
              <Route path="compute/computers" element={<ComputeComputers pendingPoolReturn={navigation.pendingPoolReturn} onPoolReturnFinished={finishPoolReturn} />} />
              <Route path="compute/computers/:computerId" element={<Computer interrupted={interrupted} />} />
              <Route path="power-cells" element={<LegacyCompute />} />
              <Route path="power-cells/*" element={<LegacyCompute />} />
              <Route path="organizations" element={<Organizations />} />
              <Route path="organizations/:companyId" element={<Organization memberSearchDrafts={memberSearchDrafts} />}>
                <Route path="members" element={null} />
              </Route>
              <Route path="invitations/:token" element={<Invitation />} />
              <Route path="profile" element={<Profile key={user?.id} />} />
              <Route path="api-tokens" element={<LegacyApiTokens />} />
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
const root = createRoot(document.getElementById("root")!);
// Capture the loaded entry and subscribe to history before an immediate Back after reload.
flushSync(() => {
  root.render(
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
});
