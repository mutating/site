import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { allPages, api, ApiError, json, request } from "../lib/api";
import { useData } from "../lib/useData";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import { memberName } from "../lib/permissions";
import type { ApiToken, Company } from "../lib/types";
import { CreateTokenDialog } from "../components/CreateTokenDialog";
import {
  ConfirmDialog,
  Empty,
  ErrorBox,
  Heading,
  Icon,
  Loading,
} from "../components/UI";

export function ApiTokens() {
  const { user } = useSession();
  const { t, locale } = useI18n();
  const [params, setParams] = useSearchParams();
  const owner = params.get("owner") || "all";
  const page = Math.max(
    1,
    Math.min(1_000_000, Number.parseInt(params.get("page") || "1", 10) || 1),
  );
  const key = `${user!.id}/${owner}/${page}`;
  const [owners, setOwners] = useState<{ userId: string; items: Pick<Company, "id" | "name">[] } | null>(null);
  const state = useData(
    async (signal) => {
      const companies = (await allPages<Company>("companies", signal)).filter(
        (company) =>
          company.my_permissions.includes("tokens.read") ||
          company.my_permissions.includes("tokens.manage"),
      );
      if (!signal.aborted) setOwners({ userId: user!.id, items: companies.map(({ id, name }) => ({ id, name })) });
      const company = companies.find((value) => value.id === owner);
      if (owner !== "all" && owner !== "personal" && !company)
        throw new ApiError(404, "");
      const canRead =
        !company || company.my_permissions.includes("tokens.read");
      const query = new URLSearchParams({
        limit: "10",
        offset: String((page - 1) * 10),
      });
      if (owner === "personal") query.set("user_id", user!.id);
      else if (company) query.set("company_id", company.id);
      const result = canRead
        ? await request<ApiToken[]>(`api-tokens?${query}`, { signal })
        : { data: [], total: 0 };
      return {
        key,
        companies,
        tokens: result.data,
        total: result.total || 0,
        canRead,
      };
    },
    key,
    true,
  );
  const data = state.data?.key === key ? state.data : null;
  const ownerChoices = owners?.userId === user!.id ? owners.items : [];
  const selected = data?.companies.find((company) => company.id === owner);
  const canManage =
    owner === "all" ||
    owner === "personal" ||
    !!selected?.my_permissions.includes("tokens.manage");
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);
  const pages = Math.max(1, Math.ceil((data?.total || 0) / 10));
  const updatePage = (value: number, replace = false) => {
    const next = new URLSearchParams(params);
    if (value === 1) next.delete("page");
    else next.set("page", String(value));
    setParams(next, { replace });
  };
  useEffect(() => {
    if (data && !state.loading && page > pages) updatePage(pages, true);
  }, [data, state.loading, page, pages]);
  const personalName =
    [user!.first_name, user!.last_name].filter(Boolean).join(" ") ||
    user!.nickname ||
    user!.email;
  const ownerName = (token: ApiToken) =>
    token.company_id
      ? data?.companies.find((company) => company.id === token.company_id)
          ?.name || token.company_id
      : personalName;
  const status = (token: ApiToken) =>
    token.revoked_at
      ? "tokenRevoked"
      : Date.parse(token.expires_at) <= Date.now()
        ? "tokenExpired"
        : "tokenActive";
  return (
    <div className="api-tokens-page">
      <Heading index={`06 / ${t("apiTokens")}`} title={t("availableTokens")}>
        {t("tokensLead")}
      </Heading>
      <div className="token-registry-toolbar">
        <div className="token-registry-filter">
          <label className="field">
            {t("tokenOwnerFilter")}
            <select
              aria-label={t("tokenOwnerFilter")}
              value={owner}
              disabled={!data && state.loading}
              onChange={(event) => {
                const next = new URLSearchParams(params);
                next.set("owner", event.target.value);
                next.delete("page");
                setParams(next);
              }}
            >
              <option value="all">{t("tokenAllOwners")}</option>
              <option value="personal">
                {t("tokenPersonal")} · {personalName}
              </option>
              {owner !== "all" && owner !== "personal" && !ownerChoices.some(company => company.id === owner) && <option value={owner}>{t("tokenCompany")} · {owner}</option>}
              {ownerChoices.map((company) => (
                <option key={company.id} value={company.id}>
                  {t("tokenCompany")} · {company.name}
                </option>
              ))}
            </select>
          </label>
          <button
            className="button secondary token-refresh"
            aria-label={t("refresh")}
            title={t("refresh")}
            disabled={state.loading}
            onClick={state.reload}
          >
            <Icon name="refresh" size={15} />
          </button>
        </div>
        <button
          className="button primary"
          disabled={!data || !canManage}
          onClick={(event) => {
            event.currentTarget.focus({ preventScroll: true });
            setCreating(true);
          }}
        >
          <Icon name="plus" size={17} />
          {t("createToken")}
        </button>
      </div>
      {selected &&
        (!selected.my_permissions.includes("tokens.manage") ||
          !data?.canRead) && (
          <p className="token-permission-note">
            {t(data?.canRead ? "tokenReadOnlyHelp" : "tokenNoReadHelp")}
          </p>
        )}
      <ErrorBox error={state.error} retry={state.reload} />
      {!data || page > pages ? (
        state.loading ? (
          <Loading />
        ) : null
      ) : !data.canRead ? null : !data.tokens.length ? (
        <Empty icon="key" title={t("noApiTokens")}>
          {t("noApiTokensLead")}
        </Empty>
      ) : (
        <>
          <div
            className="token-registry"
            aria-label={t("apiTokens")}
            aria-busy={state.loading}
          >
            <div className="token-registry-columns" aria-hidden="true">
              <span>{t("tokenNameIssuer")}</span>
              <span>{t("tokenOwner")}</span>
              <span>{t("tokenAccess")}</span>
              <span>{t("tokenValidUntil")}</span>
              <span />
            </div>
            {data.tokens.map((token) => (
              <article
                className="token-registry-row"
                key={token.id}
                aria-label={token.name}
              >
                <div className="token-registry-name">
                  <h2>{token.name}</h2>
                  <small>
                    {t("tokenIssuer")}: {memberName(token.issuer)}
                  </small>
                  {token.purpose === "legacy" && (
                    <small className="token-legacy">{t("tokenLegacy")}</small>
                  )}
                </div>
                <div className="token-registry-owner">
                  <div>
                    <small>
                      {t(token.company_id ? "tokenCompany" : "tokenPersonal")}
                    </small>
                    <strong>{ownerName(token)}</strong>
                  </div>
                </div>
                <div className="token-registry-scopes">
                  {token.scopes
                    .filter(
                      (scope) =>
                        scope !== "execute" || !token.scopes.includes("read"),
                    )
                    .map((scope) => (
                      <span key={scope}>
                        {t(
                          scope === "read"
                            ? token.scopes.includes("execute")
                              ? "tokenReadCommands"
                              : "tokenReadScope"
                            : scope === "execute"
                              ? "tokenExecuteOnly"
                              : "tokenManageOnly",
                        )}
                      </span>
                    ))}
                </div>
                <div className="token-registry-expiry">
                  <time dateTime={token.expires_at}>
                    {new Intl.DateTimeFormat(locale, {
                      dateStyle: "medium",
                    }).format(new Date(token.expires_at))}
                  </time>
                  <span
                    className={`token-status ${status(token) === "tokenActive" ? "is-active" : ""}`}
                  >
                    {t(status(token))}
                  </span>
                </div>
                <div className="token-registry-action">
                  {!token.company_id ||
                  data.companies
                    .find((company) => company.id === token.company_id)
                    ?.my_permissions.includes("tokens.manage") ? (
                    <button
                      className="text-button danger-text"
                      disabled={
                        !!token.revoked_at || status(token) === "tokenExpired"
                      }
                      onClick={(event) => {
                        event.currentTarget.focus({ preventScroll: true });
                        setRevoking(token);
                      }}
                    >
                      {t("revokeToken")}
                    </button>
                  ) : (
                    <span className="field-help">{t("tokenViewOnly")}</span>
                  )}
                </div>
              </article>
            ))}
          </div>
          {pages > 1 && (
            <nav className="pagination" aria-label={t("page")}>
              <span>
                {t("page")} {page} {t("of")} {pages}
              </span>
              <div>
                <button
                  className="button secondary compact"
                  disabled={page <= 1 || state.loading}
                  onClick={() => updatePage(page - 1)}
                >
                  ← {t("previous")}
                </button>
                <button
                  className="button secondary compact"
                  disabled={page >= pages || state.loading}
                  onClick={() => updatePage(page + 1)}
                >
                  {t("next")} →
                </button>
              </div>
            </nav>
          )}
        </>
      )}
      <div className="token-owner-legend">
        <span>
          <Icon name="user" size={16} />
          {t("tokenPersonalLegend")}
        </span>
        <span>
          <Icon name="building" size={16} />
          {t("tokenCompanyLegend")}
        </span>
      </div>
      {creating && data && (
        <CreateTokenDialog
          companies={data.companies}
          initialCompanyId={selected?.id}
          onCreated={state.reload}
          onClose={() => setCreating(false)}
        />
      )}
      {revoking && (
        <ConfirmDialog
          title={t("revokeToken")}
          value={revoking.name}
          warning={`${t("tokenOwner")}: ${ownerName(revoking)}. ${t("tokenRevokeHelp")}${revoking.purpose === "legacy" ? ` ${t("tokenLegacyHelp")}` : ""}`}
          onClose={() => setRevoking(null)}
          onConfirm={async () => {
            await api(
              `access-tokens/${revoking.id}`,
              json("PATCH", { revoke: true }),
            );
            state.reload();
          }}
        />
      )}
    </div>
  );
}
