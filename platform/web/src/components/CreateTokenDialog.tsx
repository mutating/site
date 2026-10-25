import { useEffect, useRef, useState } from "react";
import { api, json } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import { memberName } from "../lib/permissions";
import type { Company, IssuedApiToken, TokenScope } from "../lib/types";
import { ErrorBox, Icon } from "./UI";

export function CreateTokenDialog({
  companies,
  initialCompanyId,
  onRefresh,
  onClose,
}: {
  companies: Pick<Company, "id" | "name" | "my_permissions">[];
  initialCompanyId?: string;
  onRefresh: (failure?: unknown) => void;
  onClose: () => void;
}) {
  const { user } = useSession();
  const { t, locale } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const inFlight = useRef(false);
  const [kind, setKind] = useState(initialCompanyId ? "company" : "personal");
  const manageable = companies.filter((company) =>
    company.my_permissions.includes("tokens.manage"),
  );
  const [companyId, setCompanyId] = useState(
    initialCompanyId || manageable[0]?.id || "",
  );
  const [name, setName] = useState("");
  const [days, setDays] = useState("30");
  const [execute, setExecute] = useState(true);
  const [manage, setManage] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [issued, setIssued] = useState<{
    value: IssuedApiToken;
    owner: string;
  } | null>(null);
  const [copyStatus, setCopyStatus] = useState<"tokenCopied" | "tokenCopyFallback" | null>(null);
  const secret = useRef<HTMLInputElement>(null);
  const resultTitle = useRef<HTMLHeadingElement>(null);
  const company = companies.find((value) => value.id === companyId);
  const personalName =
    [user!.first_name, user!.last_name].filter(Boolean).join(" ") ||
    user!.nickname ||
    user!.email;
  const ownerName = kind === "personal" ? personalName : company?.name || "";
  const canCreate =
    kind === "personal" || !!company?.my_permissions.includes("tokens.manage");
  useEffect(() => {
    const dialog = ref.current!;
    const trigger = document.activeElement;
    dialog.showModal();
    return () => {
      dialog.close();
      if (trigger instanceof HTMLElement && trigger.isConnected)
        trigger.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => {
    if (issued) {
      if (ref.current) ref.current.scrollTop = 0;
      resultTitle.current?.focus({ preventScroll: true });
    }
  }, [issued]);
  useEffect(() => {
    const companyId = issued?.value.company_id;
    if (companyId && !companies.some((company) => company.id === companyId &&
      company.my_permissions.some((permission) => ["tokens.read", "tokens.manage"].includes(permission)))) onClose();
  }, [companies, issued, onClose]);
  useEffect(() => {
    // Native focus scrolling must reserve the sticky panel's space, including errors.
    const dialog = ref.current!;
    const actions = dialog.querySelector<HTMLElement>(".token-dialog-actions");
    if (!actions) return;
    const update = () => dialog.style.setProperty(
      "--token-actions-height", `${actions.getBoundingClientRect().height}px`,
    );
    const observer = new ResizeObserver(update);
    observer.observe(actions);
    update();
    return () => {
      observer.disconnect();
      dialog.style.removeProperty("--token-actions-height");
    };
  }, [issued]);
  return (
    <dialog
      ref={ref}
      className="confirm-dialog create-token-dialog"
      aria-labelledby="create-token-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="token-dialog-top">
        {!issued && <p className="eyebrow">{t("apiAccess")}</p>}
        <button
          type="button"
          className="button secondary token-dialog-close"
          aria-label={t("closeDialog")}
          disabled={busy}
          onClick={onClose}
        >
          <Icon name="close" size={17} />
        </button>
      </div>
      {issued ? (
        <div className="issued-token">
          <div className="token-success-icon">
            <Icon name="check" size={22} />
          </div>
          <h2
            id="create-token-title"
            ref={resultTitle}
            tabIndex={-1}
            aria-describedby="issued-token-context issued-token-one-time"
          >
            {t("tokenCreated")}
            <span className="hot" aria-hidden="true">
              .
            </span>
          </h2>
          <div className="token-owner-summary" id="issued-token-context">
            <span className="eyebrow">{t("tokenOwner")}</span>
            <div className="token-owner-badge">
              <Icon
                name={issued.value.company_id ? "building" : "user"}
                size={18}
              />
              <div>
                <small>
                  {t(
                    issued.value.company_id ? "tokenCompany" : "tokenPersonal",
                  )}
                </small>
                <strong>{issued.owner}</strong>
              </div>
            </div>
            <small>
              {t("tokenIssuer")}: {memberName(issued.value.issuer)}
            </small>
          </div>
          <p className="token-issued-details">
            <strong>{issued.value.name}</strong> ·{" "}
            {issued.value.expires_at === null ? (
              t("tokenNoExpiry")
            ) : (
              <>
                {t("tokenValidUntil")}{" "}
                <time dateTime={issued.value.expires_at}>
                  {new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(
                    new Date(issued.value.expires_at),
                  )}
                </time>
              </>
            )}
          </p>
          <label className="field">
            {t("apiTokenSecret")}
            <input
              ref={secret}
              value={issued.value.token}
              readOnly
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <button
            type="button"
            className="button primary token-full-button"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(issued.value.token);
                setCopyStatus("tokenCopied");
              } catch {
                secret.current?.select();
                setCopyStatus("tokenCopyFallback");
              }
            }}
          >
            <Icon name="copy" size={15} />
            {t("tokenCopy")}
          </button>
          <p className="field-help token-one-time" id="issued-token-one-time">
            {t("tokenOneTime")}
          </p>
          <p role="status" className="field-help token-copy-status">
            {copyStatus && t(copyStatus)}
          </p>
          <button
            type="button"
            className="button secondary token-full-button"
            onClick={onClose}
          >
            {t("tokenDone")}
          </button>
        </div>
      ) : (
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (inFlight.current || !name.trim() || !canCreate) return;
            inFlight.current = true;
            setBusy(true);
            setError(null);
            const owner = ownerName;
            const scopes: TokenScope[] = execute
              ? ["read", "execute"]
              : ["read"];
            if (execute && manage) scopes.push("manage");
            try {
              const value = await api<IssuedApiToken>(
                "api-tokens",
                json("POST", {
                  ...(kind === "personal"
                    ? { user_id: user!.id }
                    : { company_id: companyId }),
                  name: name.trim(),
                  scopes,
                  expires_in_seconds:
                    days === "unlimited" ? null : Number(days) * 86400,
                }),
              );
              setIssued({ value, owner });
              setBusy(false);
              onRefresh();
            } catch (failure) {
              setError(failure);
              setBusy(false);
              inFlight.current = false;
              onRefresh(failure);
            }
          }}
        >
          <h2 id="create-token-title">
            {t("createToken")}
            <span className="hot" aria-hidden="true">
              .
            </span>
          </h2>
          <fieldset className="token-owner-choices" disabled={busy}>
            <legend>{t("tokenOwner")}</legend>
            <label>
              <input
                type="radio"
                name="token-owner"
                value="personal"
                checked={kind === "personal"}
                onChange={() => setKind("personal")}
              />
              <Icon name="user" />
              <span>
                <strong>{t("tokenPersonal")}</strong>
                <small>{personalName}</small>
              </span>
            </label>
            <label>
              <input
                type="radio"
                name="token-owner"
                value="company"
                checked={kind === "company"}
                disabled={!manageable.length}
                onChange={() => setKind("company")}
              />
              <Icon name="building" />
              <span>
                <strong>{t("tokenCompany")}</strong>
                <small>{company?.name || t("tokenNoCompanyChoice")}</small>
              </span>
            </label>
          </fieldset>
          {kind === "company" && manageable.length > 1 && (
            <label className="field">
              {t("tokenOrganization")}
              <select
                value={companyId}
                disabled={busy}
                onChange={(event) => setCompanyId(event.target.value)}
              >
                {companies.map((value) => (
                  <option
                    key={value.id}
                    value={value.id}
                    disabled={!value.my_permissions.includes("tokens.manage")}
                  >
                    {value.name}
                    {!value.my_permissions.includes("tokens.manage")
                      ? ` · ${t("tokenViewOnly")}`
                      : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="token-owner-context">
            <div className="token-owner-badge">
              <Icon
                name={kind === "personal" ? "user" : "building"}
                size={18}
              />
              <div>
                <small>
                  {t(kind === "personal" ? "tokenPersonal" : "tokenCompany")}
                </small>
                <strong>{ownerName}</strong>
              </div>
            </div>
            <p>
              {t(
                kind === "personal" ? "tokenPersonalHelp" : "tokenCompanyHelp",
              )}
            </p>
            <small>
              {t(
                kind === "personal" ? "tokenIssuingYou" : "tokenIssuingMember",
              )}{" "}
              · {personalName}
            </small>
          </div>
          {!manageable.length && (
            <p className="field-help">{t("tokenNoManageCompanies")}</p>
          )}
          <label className="field">
            {t("tokenName")}
            <input
              autoFocus
              name="name"
              maxLength={100}
              required
              value={name}
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
              placeholder={t("tokenNamePlaceholder")}
            />
          </label>
          <label className="field">
            {t("tokenLifetime")}
            <select
              value={days}
              disabled={busy}
              onChange={(event) => setDays(event.target.value)}
            >
              {[7, 30, 90, 365].map((value) => (
                <option key={value} value={value}>
                  {value === 365 ? t("tokenYear") : `${value} ${t("tokenDays")}`}
                </option>
              ))}
              <option value="unlimited">{t("tokenNoExpiry")}</option>
            </select>
          </label>
          <fieldset className="token-scope-choices" disabled={busy}>
            <legend>{t("tokenScopes")}</legend>
            <label>
              <input
                type="radio"
                name="token-scopes"
                aria-label={t("tokenExecute")}
                aria-describedby="token-execute-help"
                checked={execute}
                onChange={() => setExecute(true)}
              />
              <span>
                <strong>{t("tokenExecute")}</strong>
                <small id="token-execute-help">{t("tokenExecuteHelp")}</small>
              </span>
            </label>
            <label>
              <input
                type="radio"
                name="token-scopes"
                aria-label={t("tokenRead")}
                aria-describedby="token-read-help"
                checked={!execute}
                onChange={() => {
                  setExecute(false);
                  setManage(false);
                }}
              />
              <span>
                <strong>{t("tokenRead")}</strong>
                <small id="token-read-help">{t("tokenReadHelp")}</small>
              </span>
            </label>
          </fieldset>
          <details className="token-advanced">
            <summary>{t("tokenAdvanced")}</summary>
            <label>
              <input
                type="checkbox"
                checked={manage}
                disabled={busy || !execute}
                onChange={(event) => setManage(event.target.checked)}
              />
              {t("tokenManage")}
            </label>
            {!execute && (
              <p className="field-help">{t("tokenManageReadOnlyHelp")}</p>
            )}
          </details>
          <div className="token-dialog-actions">
            <ErrorBox error={error} />
            <button
              className="button primary token-full-button"
              disabled={busy || !name.trim() || !canCreate}
            >
              <Icon name="key" size={15} />
              {t(
                busy
                  ? "waiting"
                  : kind === "personal"
                    ? "tokenCreatePersonal"
                    : "tokenCreateCompany",
              )}
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}
