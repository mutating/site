import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { allPages, api, ApiError, json, request } from "../lib/api";
import { useData, useRefreshWhileVisible } from "../lib/useData";
import { useReloadOnOrganizationChange } from "../lib/useOrganizationDirectory";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import { memberName } from "../lib/permissions";
import type { ApiToken, Company } from "../lib/types";
import { CreateTokenDialog } from "../components/CreateTokenDialog";
import { Select } from "../components/Select";
import {
  ConfirmDialog,
  Empty,
  ErrorBox,
  Icon,
  Loading,
  RetryErrorBox,
} from "../components/UI";

export function ApiTokens({ company, check, onCheckPermissions }: {
  company?: Company;
  check?: { error: unknown; retry: () => void; scoped?: boolean };
  onCheckPermissions?: (error?: ApiError) => void;
} = {}) {
  const { user } = useSession();
  const { t, locale } = useI18n();
  const checking = !!check;
  const checkingRegistry = checking && !check.scoped;
  const [urlParams, setUrlParams] = useSearchParams();
  const [localParams, setLocalParams] = useState(() => new URLSearchParams());
  const params = company ? localParams : urlParams;
  function setParams(next: URLSearchParams, options?: { replace?: boolean }) {
    if (company) setLocalParams(next);
    else setUrlParams(next, options);
  }
  const owner = company?.id ?? (params.get("owner") || "all");
  const filterStatus = params.get("status") === "active" ? "active" : "all";
  const page = Math.max(
    1,
    Math.min(1_000_000, Number.parseInt(params.get("page") || "1", 10) || 1),
  );
  const companyKey = company ? JSON.stringify([company.id, company.name, company.my_permissions]) : "";
  const pageKey = (index: number) => `${user!.id}/${owner}/${filterStatus}/${index}/${companyKey}`;
  const key = pageKey(page);
  const registryFocus = useRef<HTMLDivElement>(null);
  const registry = useRef<HTMLDivElement>(null);
  const focusRegistryAfterRetry = useCallback(() => {
    if (document.activeElement === document.body) registryFocus.current?.focus({ preventScroll: true });
  }, []);
  const restoreRegistryFocus = company ? () => {
    const focused = document.activeElement;
    if (focused?.matches(".token-registry-row") && registry.current?.contains(focused)) return;
    registryFocus.current?.focus({ preventScroll: true });
  } : undefined;
  const pendingFocus = useRef<{ key: string; tokenId?: string } | null>(null);
  const [owners, setOwners] = useState<{ userId: string; companyKey: string; items: Pick<Company, "id" | "name" | "my_permissions">[] } | null>(null);
  const state = useData<{ key: string; companies: Company[]; tokens: ApiToken[]; total: number; canRead: boolean }>(
    async (signal) => {
      const entries = company ? [company] : await allPages<Company>("companies", signal);
      const companies = entries.filter(
        (entry) =>
          entry.my_permissions.includes("tokens.read") ||
          entry.my_permissions.includes("tokens.manage"),
      );
      if (!signal.aborted) setOwners({ userId: user!.id, companyKey, items: companies });
      const selected = companies.find((value) => value.id === owner);
      if (owner !== "all" && owner !== "personal" && !selected)
        throw new ApiError(404, "");
      const canRead =
        !selected || selected.my_permissions.includes("tokens.read");
      const query = new URLSearchParams({
        limit: "10",
        offset: String((page - 1) * 10),
      });
      if (owner === "personal") query.set("user_id", user!.id);
      else if (selected) query.set("company_id", selected.id);
      if (filterStatus === "active") query.set("status", "active");
      const result = canRead
        ? await request<ApiToken[]>(`api-tokens?${query}`, { signal }).catch((error) => {
          if (company && !signal.aborted && error instanceof ApiError && (error.status === 403 || error.status === 404)) {
            state.update(null);
            onCheckPermissions?.(error);
          }
          throw error;
        })
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
    { retainError: !!company },
  );
  useRefreshWhileVisible(state.reload, !!company);
  const ownerChoices = owners?.userId === user!.id && owners.companyKey === companyKey ? owners.items : company ? [company] : [];
  const readableCompany = (id: string) => ownerChoices.some((company) =>
    company.id === id && company.my_permissions.includes("tokens.read"));
  // Only a newer owners snapshot can invalidate an already authorized registry response.
  const lostAccess = state.data?.key === key && ownerChoices !== state.data.companies && (
    state.data.companies.some((previous) => (owner === "all" || previous.id === owner) &&
      previous.my_permissions.includes("tokens.read") && !readableCompany(previous.id)) ||
    state.data.tokens.some((token) => token.company_id && !readableCompany(token.company_id))
  );
  const denied = state.error instanceof ApiError && (state.error.status === 403 || state.error.status === 404);
  const data = !checkingRegistry && !denied && state.data?.key === key && !lostAccess ? state.data : null;
  const selected = company ? ownerChoices[0] : data?.companies.find((company) => company.id === owner);
  const canManage = company
    ? !!selected?.my_permissions.includes("tokens.manage")
    : owner === "all" || owner === "personal" || !!selected?.my_permissions.includes("tokens.manage");
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);
  useReloadOnOrganizationChange(user!.id, owner === "personal" ? null : owner, (change) => {
    if (company && change.type !== "membersChanged") return;
    if (change.type === "membersChanged") {
      const row = document.activeElement?.closest<HTMLElement>(".token-registry-row");
      if (row && registry.current?.contains(row)) pendingFocus.current = { key, tokenId: row.dataset.tokenId };
    }
    if (change.type === "removed" || change.type === "membershipUpdated") {
      state.update((current) => {
        if (current?.key !== key) return current;
        if (owner !== "all") return null;
        return {
          ...current,
          companies: current.companies.filter((company) => company.id !== change.companyId),
          tokens: current.tokens.filter((token) => token.company_id !== change.companyId),
        };
      });
      setRevoking((token) => token?.company_id === change.companyId ? null : token);
    }
    state.reload();
  }, { includeMemberChanges: true });
  useEffect(() => {
    setCreating(false);
    setRevoking(null);
  }, [user?.id, owner, filterStatus]);
  useEffect(() => {
    // Fresh company permissions can deny access before the token request finishes.
    if (lostAccess || denied) {
      state.update(null);
      setRevoking(null);
      // The profile dialog can issue a personal token while the organization filter loses access.
      if (denied && company) setCreating(false);
    }
  }, [company, denied, lostAccess, state.update]);
  const refresh = (failure?: unknown) => {
    if (failure instanceof ApiError && failure.status === 404) {
      state.update(null);
      setCreating(false);
      setRevoking(null);
    }
    if (company && failure instanceof ApiError && (failure.status === 403 || failure.status === 404)) {
      onCheckPermissions?.();
    }
    state.reload();
  };
  const pages = Math.max(1, Math.ceil((data?.total || 0) / 10));
  const updatePage = (value: number, replace = false, focusResult = false) => {
    if (focusResult) pendingFocus.current = { key: pageKey(value) };
    const next = new URLSearchParams(params);
    if (value === 1) next.delete("page");
    else next.set("page", String(value));
    setParams(next, { replace });
  };
  useEffect(() => {
    if (data && !state.loading && page > pages) updatePage(pages, true);
  }, [data, state.loading, page, pages]);
  useEffect(() => {
    const pending = pendingFocus.current;
    if (!pending) return;
    if (pending.key !== key || (!state.loading && state.error)) {
      pendingFocus.current = null;
      return;
    }
    if (!data || state.loading) return;
    pendingFocus.current = null;
    // A list refresh can remove or disable the trigger; respect any newer focus.
    const focused = document.activeElement;
    const disabledAction = focused instanceof HTMLButtonElement && focused.disabled && registry.current?.contains(focused);
    if (focused !== document.body && focused !== registryFocus.current && !disabledAction) return;
    const row = pending.tokenId && Array.from(registry.current?.querySelectorAll<HTMLElement>(".token-registry-row") || [])
      .find(element => element.dataset.tokenId === pending.tokenId);
    if (row) row.focus({ preventScroll: true });
    else registryFocus.current?.focus();
  }, [key, data, state.loading, state.error]);
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
      : token.expires_at !== null && Date.parse(token.expires_at) <= Date.now()
        ? "tokenExpired"
        : "tokenActive";
  const refreshing = checkingRegistry || state.loading;
  const registryError = checkingRegistry ? check?.error : state.error ?? check?.error;
  const retryRegistry = !checkingRegistry && state.error ? state.reload : check?.retry;
  const createToken = (
    <button
      className="button primary token-create-button"
      disabled={checking || !data || !canManage || (!!company && state.loading)}
      onClick={(event) => {
        event.currentTarget.focus({ preventScroll: true });
        setCreating(true);
      }}
    >
      <Icon name="plus" size={17} />
      {t("createToken")}
    </button>
  );
  return (
    <div className="api-tokens-page">
      <div
        ref={registryFocus}
        className="token-registry-toolbar"
        role="group"
        aria-label={t("availableTokens")}
        tabIndex={-1}
      >
        <div className="token-create-compact">{createToken}</div>
        <div className="token-registry-filter">
          {!company && <div className="field token-owner-filter">
            <Select
              aria-label={t("tokenOwnerFilter")}
              value={owner}
              disabled={!owners && state.loading}
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
            </Select>
          </div>}
          <div className="field token-status-filter">
            <Select
              aria-label={t("tokenStatusFilter")}
              value={filterStatus}
              disabled={checkingRegistry}
              onChange={(event) => {
                const next = new URLSearchParams(params);
                if (event.target.value === "active") next.set("status", "active");
                else next.delete("status");
                next.delete("page");
                setParams(next);
              }}
            >
              <option value="all">{t("tokenAllStatuses")}</option>
              <option value="active">{t("tokenActiveOnly")}</option>
            </Select>
          </div>
          <button
            className="button secondary token-refresh"
            aria-label={t("refresh")}
            title={t("refresh")}
            disabled={!company && refreshing}
            aria-disabled={!!company && refreshing || undefined}
            onClick={() => {
              if (refreshing) return;
              if (!checking && canManage) onCheckPermissions?.();
              refresh();
            }}
          >
            <Icon name="refresh" size={15} />
          </button>
        </div>
        <div className="token-create-wide">{createToken}</div>
      </div>
      {data && selected &&
        (!selected.my_permissions.includes("tokens.manage") ||
          !data?.canRead) && (
          <p className="token-permission-note">
            {t(data?.canRead ? "tokenReadOnlyHelp" : "tokenNoReadHelp")}
          </p>
        )}
      {!!registryError && (company && retryRegistry
        ? <RetryErrorBox error={registryError} retry={retryRegistry} restoreFocus={focusRegistryAfterRetry} />
        : <ErrorBox error={registryError} retry={retryRegistry} />)}
      {checkingRegistry ? (
        !registryError && <Loading />
      ) : !data || page > pages ? (
        state.loading ? (
          <Loading />
        ) : null
      ) : !data.canRead ? null : !data.tokens.length ? (
        <Empty
          icon="key"
          title={t(filterStatus === "active" ? "noActiveApiTokens" : "noApiTokens")}
        >
          {canManage && t(filterStatus === "active" ? "noActiveApiTokensLead" : "noApiTokensLead")}
        </Empty>
      ) : (
        <>
          <div
            ref={registry}
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
                data-token-id={token.id}
                tabIndex={-1}
                aria-label={token.name}
              >
                <div className="token-registry-name">
                  <h2>{token.name}</h2>
                  <small>
                    {t("tokenIssuer")}: {memberName(token.issuer)}
                  </small>
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
                  {token.expires_at === null ? (
                    <span>{t("tokenNoExpiry")}</span>
                  ) : (
                    <time dateTime={token.expires_at}>
                      {new Intl.DateTimeFormat(locale, {
                        dateStyle: "medium",
                      }).format(new Date(token.expires_at))}
                    </time>
                  )}
                  <span
                    className={`token-status ${status(token) === "tokenActive" ? "is-active" : ""}`}
                  >
                    {t(status(token))}
                  </span>
                </div>
                <div className="token-registry-action">
                  {!token.company_id ||
                  ownerChoices
                    .find((company) => company.id === token.company_id)
                    ?.my_permissions.includes("tokens.manage") ? (
                    <button
                      className="text-button danger-text"
                      disabled={
                        !!token.revoked_at || status(token) === "tokenExpired"
                      }
                      aria-disabled={checking || !!company && state.loading || undefined}
                      onClick={(event) => {
                        if (checking || company && state.loading) return;
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
                  onClick={() => updatePage(page - 1, false, true)}
                >
                  ← {t("previous")}
                </button>
                <button
                  className="button secondary compact"
                  disabled={page >= pages || state.loading}
                  onClick={() => updatePage(page + 1, false, true)}
                >
                  {t("next")} →
                </button>
              </div>
            </nav>
          )}
        </>
      )}
      {creating && (!company || !denied) && (
        <CreateTokenDialog
          companies={ownerChoices}
          initialCompanyId={selected?.id}
          fixedCompanyId={company?.id}
          check={check}
          restoreFocus={restoreRegistryFocus}
          onRefresh={refresh}
          onClose={() => setCreating(false)}
        />
      )}
      {revoking && !denied && (
        <ConfirmDialog
          title={t("revokeToken")}
          value={revoking.name}
          warning={`${t("tokenOwner")}: ${ownerName(revoking)}. ${t("tokenRevokeHelp")}`}
          restoreFocus={restoreRegistryFocus}
          check={check}
          onClose={() => setRevoking(null)}
          onConfirm={async () => {
            try {
              await api(
                `access-tokens/${revoking.id}`,
                json("PATCH", { revoke: true }),
              );
              pendingFocus.current = { key, tokenId: revoking.id };
            } catch (failure) {
              refresh(failure);
              throw failure;
            }
            refresh();
          }}
        />
      )}
    </div>
  );
}
