import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { api, json } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import type { User } from "../lib/types";
import { Header } from "../components/Layout";
import { ErrorBox, Icon } from "../components/UI";
export function Auth({ register = false }: { register?: boolean }) {
  const { t } = useI18n();
  const { user, setUser } = useSession();
  const location = useLocation();
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const from =
    typeof location.state?.from === "string" &&
    location.state.from.startsWith("/") &&
    !location.state.from.startsWith("//") &&
    !/^\/(login|register)/.test(location.state.from)
      ? location.state.from
      : "/";
  if (user) return <Navigate to={from} replace />;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    const payload = Object.fromEntries(form.entries());
    if (register && !payload.nickname) delete payload.nickname;
    try {
      if (register) {
        await api<User>("users", json("POST", payload));
      }
      let profile: User;
      try {
        profile = await api<User>(
          "auth/session",
          json("POST", {
            login: register ? payload.email : payload.login,
            password: payload.password,
          }),
        );
      } catch (error) {
        if (!register) throw error;
        navigate("/login", {
          replace: true,
          state: { from, accountCreated: true, login: payload.email },
        });
        return;
      }
      setUser(profile);
      navigate(from, { replace: true });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Header />
      <main id="main" className="auth-shell">
        <section className="auth-story">
          <p className="eyebrow">
            The Mutating Company <span className="hot">/</span> {t("account")}
          </p>
          <h1>
            {t("authHeading")
              .split("\n")
              .map((line, index) => (
                <span key={line} className={index ? "hot" : ""}>
                  {line}
                </span>
              ))}
          </h1>
          <p>{t("authDescription")}</p>
          <div className="network-art" aria-hidden="true">
            <svg
              viewBox="13.5 0 572 300"
              preserveAspectRatio="xMinYMid meet"
              focusable="false"
            >
              <g fill="none" strokeLinecap="round">
                <path
                  className="network-route-ink"
                  strokeWidth="7"
                  d="M17 74H179q31 0 53 22l80 80q22 22 53 22h217"
                />
                <path
                  className="network-route-hot"
                  strokeWidth="7"
                  d="M17 228h120q32 0 55-23l112-112q23-23 55-23h223"
                />
                <path
                  className="network-route-muted"
                  strokeWidth="5"
                  d="M91 18v128q0 29 29 29h389q29 0 29 29v72"
                />
              </g>
              <g className="network-stations" strokeWidth="2.5">
                {[
                  [91, 74, "ink"],
                  [175, 74, "ink"],
                  [170, 175, "muted"],
                  [244, 153, "hot"],
                  [365, 198, "ink"],
                  [404, 70, "hot"],
                  [485, 198, "ink"],
                  [538, 70, "hot"],
                ].map(([cx, cy, color]) => (
                  <circle
                    key={`${cx}-${cy}`}
                    cx={cx}
                    cy={cy}
                    r="8"
                    className={`network-route-${color}`}
                  />
                ))}
              </g>
              <g className="network-labels">
                <text x="15" y="48">
                  {t("authDevices")}
                </text>
                <text x="582" y="251" textAnchor="end">
                  {t("authCapacity")}
                </text>
              </g>
            </svg>
          </div>
          <div className="auth-signal">
            <span className="signal-dot" />
            {t("authSignal")}
          </div>
        </section>
        <section className="auth-panel" aria-labelledby="auth-title">
          <p className="eyebrow">{register ? "01 / START" : "01 / WELCOME"}</p>
          <h2 id="auth-title">
            {t(register ? "registerTitle" : "welcome")}
            <span className="hot">.</span>
          </h2>
          <p className="muted">{t(register ? "registerLead" : "loginLead")}</p>
          {!register && location.state?.accountCreated && (
            <p className="success-message" role="status">
              {t("accountCreatedSignIn")}
            </p>
          )}
          <form onSubmit={submit}>
            {register && (
              <>
                <div className="field-row">
                  <label className="field">
                    {t("firstName")} <small>{t("optional")}</small>
                    <input
                      name="first_name"
                      autoComplete="given-name"
                      maxLength={100}
                    />
                  </label>
                  <label className="field">
                    {t("lastName")} <small>{t("optional")}</small>
                    <input
                      name="last_name"
                      autoComplete="family-name"
                      maxLength={100}
                    />
                  </label>
                </div>
                <label className="field">
                  {t("nickname")} <small>{t("optional")}</small>
                  <input
                    aria-label={t("nickname")}
                    name="nickname"
                    autoComplete="username"
                    maxLength={64}
                    pattern="[A-Za-z0-9_][A-Za-z0-9_.\-]*"
                    aria-describedby="nickname-help"
                  />
                  <span id="nickname-help" className="field-help">
                    {t("nicknameHelp")}
                  </span>
                </label>
              </>
            )}
            <label className="field">
              {t(register ? "email" : "identifier")}
              <input
                name={register ? "email" : "login"}
                type={register ? "email" : "text"}
                autoComplete={register ? "email" : "username"}
                defaultValue={
                  !register && typeof location.state?.login === "string"
                    ? location.state.login
                    : ""
                }
                required
                maxLength={320}
                autoCapitalize="none"
                spellCheck={false}
              />
            </label>
            <label className="field">
              {t("password")}
              <span className="password-field">
                <input
                  aria-label={t("password")}
                  name="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete={register ? "new-password" : "current-password"}
                  minLength={register ? 12 : undefined}
                  maxLength={1024}
                  required
                  aria-describedby={register ? "password-help" : undefined}
                />
                <button
                  type="button"
                  aria-label={t(showPassword ? "hidePassword" : "showPassword")}
                  onClick={() => setShowPassword(!showPassword)}
                >
                  <Icon name="eye" />
                </button>
              </span>
              {register && (
                <span id="password-help" className="field-help">
                  {t("passwordHelp")}
                </span>
              )}
            </label>
            <ErrorBox error={error} />
            <button className="button primary full-width" disabled={busy}>
              {t(busy ? "waiting" : register ? "register" : "login")}
              <Icon name="arrow" />
            </button>
          </form>
          <p className="auth-switch">
            {t(register ? "existingUser" : "newUser")}{" "}
            <Link to={register ? "/login" : "/register"} state={{ from }}>
              {t(register ? "login" : "register")} ↗
            </Link>
          </p>
        </section>
      </main>
    </>
  );
}
