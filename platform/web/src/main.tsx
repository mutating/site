import { useEffect } from "react";
import { createRoot } from "react-dom/client";
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
  const { loading, error, reload } = useSession();
  const { t } = useI18n();
  const location = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [location.pathname, location.search]);
  useEffect(() => {
    document.title = `${t("account")} — The Mutating Company`;
  }, [t("account")]);
  if (loading || error)
    return (
      <>
        <Header />
        <main className="boot-state">
          {loading ? <Loading /> : <ErrorBox error={error} retry={reload} />}
        </main>
      </>
    );
  return (
    <Routes>
      <Route path="login" element={<Auth key="login" />} />
      <Route path="register" element={<Auth key="register" register />} />
      <Route element={<Guard />}>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="power-cells" element={<PowerCells />} />
          <Route path="power-cells/computers" element={<Computers />} />
          <Route path="power-cells/computers/:computerId" element={<Computer />} />
          <Route path="organizations" element={<Organizations />} />
          <Route path="organizations/:companyId" element={<Organization />} />
          <Route path="invitations/:token" element={<Invitation />} />
          <Route path="profile" element={<Profile />} />
          <Route path="cloud" element={<Cloud />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </Routes>
  );
}
createRoot(document.getElementById("root")!).render(
  <I18nProvider>
    <SessionProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </SessionProvider>
  </I18nProvider>,
);
