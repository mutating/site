import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { api, json } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import type { User } from "../lib/types";
import { ErrorBox, Icon } from "../components/UI";
export function Auth({ register = false }: { register?: boolean }) {
  const { t } = useI18n();
  const { user, setUser, beginAuthentication } = useSession();
  const location = useLocation();
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const pendingRequest = useRef<AbortController | null>(null);
  // A form left through /login ↔ /register must not later change the cookie or
  // publish/navigate over the newer form's successful login.
  useEffect(() => () => pendingRequest.current?.abort(), []);
  const from =
    typeof location.state?.from === "string" &&
    location.state.from.startsWith("/") &&
    !location.state.from.startsWith("//") &&
    !/^\/(login|register)/.test(location.state.from)
      ? location.state.from
      : "/";
  const connectedPoolOrigin = location.state?.connectedPoolOrigin;
  const connectedPoolListEntry = location.state?.connectedPoolListEntry;
  const connectedPoolCorrection = location.state?.connectedPoolCorrection;
  const requiresSignIn = location.state?.requiresSignIn === true;
  const authState = { from, requiresSignIn, connectedPoolOrigin, connectedPoolListEntry, connectedPoolCorrection };
  const returnState = (userId: string) => {
    const origin = connectedPoolOrigin?.userId === userId ? connectedPoolOrigin : undefined;
    const correction = connectedPoolCorrection?.userId === userId ? connectedPoolCorrection : undefined;
    return origin || correction || typeof connectedPoolListEntry === "string"
      ? { connectedPoolOrigin: origin, connectedPoolListEntry, connectedPoolCorrection: correction } : undefined;
  };
  if (user) return <Navigate to={from} replace state={returnState(user.id)} />;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    pendingRequest.current = controller;
    const form = new FormData(event.currentTarget);
    const payload = Object.fromEntries(form.entries());
    if (register && !payload.nickname) delete payload.nickname;
    const finishAuthentication = beginAuthentication();
    try {
      if (register) {
        await api<User>("users", { ...json("POST", payload), signal: controller.signal });
        if (controller.signal.aborted) return;
      }
      let profile: User;
      try {
        profile = await api<User>(
          "auth/session",
          { ...json("POST", {
            login: register ? payload.email : payload.login,
            password: payload.password,
          }), signal: controller.signal },
        );
      } catch (error) {
        if (controller.signal.aborted) return;
        if (!register) throw error;
        navigate("/login", {
          replace: true,
          state: { ...authState, accountCreated: true, login: payload.email },
        });
        return;
      }
      if (controller.signal.aborted) return;
      setUser(profile);
      navigate(from, { replace: true, state: returnState(profile.id) });
    } catch (e) {
      if (controller.signal.aborted) return;
      setError(e);
    } finally {
      finishAuthentication();
      if (pendingRequest.current === controller) pendingRequest.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return (
    <>
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
                  {index === 0 ? " " : null}
                </span>
              ))}
          </h1>
          <p>{t("authDescription")}</p>
        </section>
        <div className="auth-stage">
          <div className="network-art" aria-hidden="true">
            <svg
              viewBox="0 0 1600 430"
              preserveAspectRatio="none"
              focusable="false"
            >
              <g fill="none" strokeLinecap="round">
                <path
                  className="network-route-ink"
                  strokeWidth="8"
                  d="M0 150H420q40 0 70 30l120 90q30 24 70 24H1600"
                />
                <path
                  className="network-route-hot"
                  strokeWidth="8"
                  d="M0 360H300q45 0 78-33l170-170q32-31 78-31H1600"
                />
                <path
                  className="network-route-muted"
                  strokeWidth="6"
                  d="M300 0V190q0 35 35 35H1230q35 0 35 35V430"
                />
              </g>
            </svg>
            {[
              [18.75, 34.8837, "ink"],
              [30.625, 41.8605, "ink"],
              [42.5, 68.3721, "ink"],
              [81.25, 68.3721, "ink"],
              [12.5, 83.7209, "hot"],
              [28.75, 56.9767, "hot"],
              [39.125, 29.3023, "hot"],
              [75, 29.3023, "hot"],
              [90.625, 29.3023, "hot"],
              [18.75, 44.186, "muted"],
              [50, 52.3256, "muted"],
              [79.0625, 60.4651, "muted"],
            ].map(([left, top, color]) => (
              <span
                key={`${left}-${top}`}
                className={`network-node network-node--${color}`}
                style={{ left: `${left}%`, top: `${top}%` }}
              />
            ))}
            <div className="network-labels">
              <span className="network-label network-label--devices">
                {t("authDevices")}
              </span>
              <span className="network-label network-label--capacity">
                {t("authCapacity")}
              </span>
            </div>
          </div>
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
              <Link to={register ? "/login" : "/register"} state={authState}
                replace={requiresSignIn}>
                {t(register ? "login" : "register")} ↗
              </Link>
            </p>
          </section>
        </div>
      </main>
    </>
  );
}
