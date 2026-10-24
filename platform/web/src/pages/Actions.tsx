import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, json } from "../lib/api";
import { useData } from "../lib/useData";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import { GITHUB_DISCOVERY_TIMEOUT_MS, githubAuthorizationURL, type CatalogRepository, type GitHubStatus } from "../lib/github";
import { Empty, ErrorBox, Heading, Loading } from "../components/UI";
import { connectionLabel, runLabel } from "../lib/ci";

type Catalog = { repositories: CatalogRepository[]; total_count: number; owners: string[] };
export function Actions() {
  const { t, locale } = useI18n();
  const { user } = useSession();
  const [params, setParams] = useSearchParams();
  const [filtersOpen, setFiltersOpen] = useState(() => window.matchMedia("(min-width: 641px)").matches || ["search", "owner_type", "owner", "state", "sort"].some(name => params.has(name)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const refresh = useRef(false);
  const status = useData(signal => api<GitHubStatus>("github/status", { signal }), user!.id);
  const query = new URLSearchParams();
  for (const name of ["search", "owner_type", "owner", "state", "sort", "per_page", "page"]) if (params.has(name)) query.set(name, params.get(name)!);
  const key = `${user!.id}/${status.data?.account?.id ?? ""}/${query}`;
  const catalog = useData(async signal => {
    if (!status.data?.account) return { key, data: null };
    if (refresh.current) { query.set("refresh", "true"); refresh.current = false; }
    return { key, data: await api<Catalog>(`github/repositories?${query}`, { signal }, GITHUB_DISCOVERY_TIMEOUT_MS) };
  }, key, true);
  const data = catalog.data?.key === key ? catalog.data.data : null;
  const owner = params.get("owner") ?? "";
  const owners = Array.from(new Set([...(data?.owners ?? []), owner].filter(Boolean)));
  const page = Math.max(1, Number(params.get("page")) || 1);
  const size = Number(params.get("per_page")) || 20;
  const pages = Math.max(1, Math.ceil((data?.total_count ?? 0) / size));
  const update = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value); else next.delete(name);
    if (name !== "page") next.delete("page");
    setParams(next, { replace: name === "search", state: { keepScroll: name === "search" } });
  };
  useEffect(() => { if (data && page > pages) update("page", String(pages)); }, [data, page, pages]);
  async function authorize(path: "authorize" | "installations/authorize") {
    setBusy(true); setError(null);
    try { const value = await api<{ url: string }>(`github/${path}`, json("POST")); if (mounted.current) window.location.assign(githubAuthorizationURL(value.url)); }
    catch (failure) { setError(failure); setBusy(false); }
  }
  async function disconnect() {
    setBusy(true); setError(null);
    try { await api("github/connection", json("DELETE")); status.update(value => value ? { ...value, account: null } : value); status.reload(); }
    catch (failure) { setError(failure); }
    finally { setBusy(false); }
  }
  const reload = () => { refresh.current = true; catalog.reload(); };
  return <>
    <Heading index={`03 / ${t("actionsTag")}`} title="Actions">{t("repositoryCatalogLead")}</Heading>
    <ErrorBox error={error} />
    {params.get("github") === "cancelled" && <p role="status">{t("githubAuthorizationCancelled")}</p>}
    {params.get("github") === "authorization_failed" && <p role="alert">{t("githubAuthorizationFailed")}</p>}
    {status.loading ? <Loading /> : status.error ? <ErrorBox error={status.error} retry={status.reload} /> : !status.data?.enabled ? <Empty title={t("actionsUnavailable")}>{t("actionsUnavailableLead")}</Empty> : <div className="actions-panels">
      <section className="panel actions-connection" aria-busy={busy}>
        <div><p className="eyebrow">GitHub</p><h2>{status.data.account ? `@${status.data.account.login}` : t("connectGitHub")}</h2><p className="muted">{t(status.data.account ? "githubConnected" : "githubConnectionLead")}</p>{status.data.account && <p className="muted">{t("githubDisconnectHelp")}</p>}</div>
        <div className="form-actions"><button className={`button ${status.data.account ? "secondary" : "primary"}`} disabled={busy} onClick={() => authorize("authorize")}>{t(status.data.account ? "reconnectGitHub" : "connectGitHub")}</button>{status.data.account && <button className="button secondary" disabled={busy} onClick={disconnect}>{t("disconnectGitHub")}</button>}</div>
      </section>
      {status.data.account && <section className="panel actions-catalog">
        <div className="actions-section-heading"><h2>{t("githubRepositories")}</h2><div className="form-actions"><button className="button secondary compact" disabled={busy || catalog.loading} onClick={reload}>{t("refresh")}</button><button className="button primary compact" disabled={busy} onClick={() => authorize("installations/authorize")}>{t("githubChooseRepositories")}</button></div></div>
        <details className="repository-filter-panel" open={filtersOpen} onToggle={event => setFiltersOpen(event.currentTarget.open)}>
          <summary>{t("repositoryFilters")}</summary>
        <div className="repository-filters">
          <label className="field repository-search">{t("repositorySearch")}<input type="search" value={params.get("search") ?? ""} onChange={event => update("search", event.target.value)} /></label>
          <label className="field">{t("repositoryType")}<select aria-label={t("repositoryType")} value={params.get("owner_type") ?? "all"} onChange={event => update("owner_type", event.target.value)}><option value="all">{t("repositoryAll")}</option><option value="personal">{t("repositoryPersonal")}</option><option value="organization">{t("repositoryOrganizations")}</option></select></label>
          <label className="field">{t("githubRepositoryOwner")}<select aria-label={t("githubRepositoryOwner")} value={owner} onChange={event => update("owner", event.target.value)}><option value="">{t("repositoryAllOwners")}</option>{owners.map(owner => <option key={owner}>{owner}</option>)}</select></label>
          <label className="field">{t("repositoryState")}<select aria-label={t("repositoryState")} value={params.get("state") ?? "all"} onChange={event => update("state", event.target.value)}><option value="all">{t("repositoryAllStates")}</option>{(["unconfigured", "pending", "connected", "disabled"] as const).map(state => <option key={state} value={state}>{t(state === "pending" ? "repositoryPendingFilter" : connectionLabel[state])}</option>)}</select></label>
          <label className="field">{t("repositorySort")}<select aria-label={t("repositorySort")} value={params.get("sort") ?? "name"} onChange={event => update("sort", event.target.value)}><option value="name">{t("repositoryNameAsc")}</option><option value="name_desc">{t("repositoryNameDesc")}</option><option value="last_run">{t("repositoryLastRun")}</option><option value="updated">{t("repositoryUpdated")}</option></select></label>
          <label className="field">{t("repositoryPageSize")}<select aria-label={t("repositoryPageSize")} value={size} onChange={event => update("per_page", event.target.value)}>{[20, 50, 100].map(value => <option key={value}>{value}</option>)}</select></label>
        </div>
        </details>
        <ErrorBox error={catalog.error} retry={reload} />
        {catalog.loading && !data ? <Loading /> : data && <>
          {!data.repositories.length && <Empty title={t("repositoryNoResults")}>{!data.owners.length ? t("githubNoRepositories") : <button className="button secondary compact" onClick={() => setParams({})}>{t("repositoryResetFilters")}</button>}</Empty>}
          <div className="repository-catalog" aria-busy={catalog.loading}>{data.repositories.map(repository => {
            const url = `/actions/repositories/${repository.id}?installation=${repository.installation_id}&return=${encodeURIComponent(`/actions?${params}`)}`;
            return <article className="repository-card" key={repository.id} aria-label={repository.full_name}>
              <div className="repository-identity"><Link to={url}><strong>{repository.full_name}</strong></Link><p className="field-help">{t(repository.private ? "githubPrivate" : "githubPublic")} · {t(repository.owner_type === "personal" ? "repositoryPersonalItem" : "repositoryOrganizationItem")}</p></div>
              <div><span className={`repository-state state-${repository.state}`}>{t(connectionLabel[repository.state])}</span><p className="field-help">{repository.last_run ? <a className="repository-run-result" data-status={repository.last_run.status} data-result={repository.last_run.reason ?? repository.last_run.status} href={repository.last_run.url} target="_blank" rel="noreferrer">{t(runLabel(repository.last_run))} · {new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(repository.last_run.created_at))}</a> : t("repositoryNoRuns")}</p></div>
              <Link className="button secondary compact" to={url}>{t(repository.state === "unconfigured" ? "repositoryConnect" : "repositoryConfigure")}</Link>
            </article>;
          })}</div>
          {pages > 1 && <nav className="pagination" aria-label={t("page")}><span>{t("page")} {page} {t("of")} {pages}</span><div><button className="button secondary compact" disabled={page <= 1 || catalog.loading} onClick={() => update("page", String(page - 1))}>← {t("previous")}</button><button className="button secondary compact" disabled={page >= pages || catalog.loading} onClick={() => update("page", String(page + 1))}>{t("next")} →</button></div></nav>}
        </>}
      </section>}
    </div>}
  </>;
}
