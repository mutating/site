import { useEffect, useRef, useState, type FormEvent } from "react";
import { allPages, api, json } from "../lib/api";
import { useData } from "../lib/useData";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import type { Company, Computer } from "../lib/types";
import type { CIRoute, CIRun, GitHubRepository } from "../lib/github";
import { ErrorBox, Loading } from "./UI";

const GiB = 1024 ** 3;
const statusLabels = { preparing: "ciPreparing", ready: "ciSelected", selected: "ciSelected", running: "ciRunning", completed: "ciCompleted", cancelled: "ciCancelled", timed_out: "ciTimedOut", failed: "ciFailed" } as const;
const conclusionLabels = { github_success: "ciSucceeded", github_failure: "ciFailed", github_cancelled: "ciCancelled", github_timed_out: "ciTimedOut", github_skipped: "ciSkipped", github_neutral: "ciNeutral", github_action_required: "ciActionRequired", github_stale: "ciStale", github_startup_failure: "ciFailed" } as const;
const reasonLabels = { runner_unavailable: "ciRunnerUnavailable", no_capacity: "ciNoCapacity", concurrency_limit: "ciConcurrencyLimit", isolate_quota: "ciIsolateQuota", route_disabled: "ciRouteDisabled", authorization_revoked: "ciAuthorizationChanged", github_unavailable: "ciGitHubUnavailable", preparation_timeout: "ciPreparationTimeout", bootstrap_cancelled: "ciPreparationCancelled", runner_timeout: "ciTimedOut", isolate_unavailable: "ciIsolateUnavailable" } as const;
function focusSection(element: HTMLElement | null) {
  element?.focus({ preventScroll: true });
  element?.scrollIntoView({ block: "start" });
}

export function CIRoutes({ installationId, repositories, discoveryReady }: { installationId?: number; repositories: GitHubRepository[]; discoveryReady: boolean }) {
  const { user } = useSession();
  const { t } = useI18n();
  const [companyId, setCompanyId] = useState("");
  const [computerIds, setComputerIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [selection, setSelected] = useState<CIRoute | null>(null);
  const [repositoryId, setRepositoryId] = useState<number | null>(null);
  const [branch, setBranch] = useState<{ repositoryId: number; value: string } | null>(null);
  const [formOpen, setFormOpen] = useState<boolean | null>(null);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const feedback = useRef<HTMLDivElement>(null);
  const routes = useData((signal) => allPages<CIRoute>("github/routes", signal), user!.id, true);
  const selected = routes.data?.find((row) => row.id === selection?.id) ?? selection;
  const companies = useData((signal) => allPages<Company>("companies", signal), user!.id);
  const computers = useData((signal) => allPages<Computer>(`computers?${companyId ? `company_id=${companyId}` : `user_id=${user!.id}`}`, signal), `${user!.id}/${companyId}`);
  const savedComputers = useData((signal) => selected?.computer_ids.length ? allPages<Computer>(`computers?${selected.company_id ? `company_id=${selected.company_id}` : `user_id=${user!.id}`}`, signal) : Promise.resolve([]), `${user!.id}/${selected?.company_id ?? ""}/${!!selected?.computer_ids.length}`);
  const workflow = useData((signal) => selected ? api<{ yaml: string }>(`github/routes/${selected.id}/workflow`, { signal }) : Promise.resolve(null), selected?.id ?? "");
  const runs = useData((signal) => selected ? api<CIRun[]>(`github/routes/${selected.id}/runs`, { signal }) : Promise.resolve([]), selected?.id ?? "");
  useEffect(() => { if (selected) focusSection(detailHeading.current); }, [selected?.id]);
  useEffect(() => { if (error || routes.error) focusSection(feedback.current); }, [error, routes.error]);
  function showDetails(route: CIRoute) {
    setSelected(route);
    if (selected?.id === route.id) focusSection(detailHeading.current);
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const route = await api<CIRoute>("github/routes", json("POST", {
        installation_id: installationId,
        repository_id: Number(form.get("repository")),
        workflow_path: form.get("workflow"),
        job_key: form.get("job"),
        ref: form.get("ref"),
        company_id: companyId || null,
        computer_ids: computerIds,
        cpu_millis: Math.round(Number(form.get("cpu")) * 1000),
        memory_bytes: Math.round(Number(form.get("memory")) * GiB),
        disk_bytes: Math.round(Number(form.get("disk")) * GiB),
        max_parallel: Number(form.get("parallel")),
        preparation_seconds: Number(form.get("wait")),
      }));
      routes.update((rows) => [...(rows ?? []).filter((row) => row.id !== route.id), route]);
      routes.reload();
      showDetails(route);
    } catch (error) {
      setError(error);
    } finally {
      setBusy(false);
    }
  }
  async function disable(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api(`github/routes/${id}`, json("DELETE"));
      routes.update((rows) => rows?.map((row) => row.id === id ? { ...row, enabled: false } : row) ?? null);
      routes.reload();
      runs.reload();
    } catch (error) { setError(error); }
    finally { setBusy(false); }
  }
  const eligible = repositories.filter((row) => row.can_administer);
  const repository = eligible.find((row) => row.id === repositoryId) ?? eligible[0];
  const powerCells = computers.data?.filter((row) => row.runtime === "powercell-ubuntu-arm64-v1") ?? [];
  const poolUnavailable = companies.loading || computers.loading || !!companies.error || !!computers.error;
  const manageablePools = companies.data?.filter((row) => row.my_permissions.includes("resources.manage") && row.my_permissions.includes("execution.run")) ?? [];
  return <section className="panel actions-repositories">
    <h2>{t("ciBindings")}</h2>
    <p className="muted">{t("ciBindingsLead")}</p>
    <div ref={feedback} tabIndex={-1} className="actions-ci-feedback"><ErrorBox error={error ?? routes.error} /></div>
    <ErrorBox error={companies.error ?? computers.error} retry={() => { companies.reload(); computers.reload(); }} />
    {discoveryReady && repositories.length > 0 && !eligible.length && <><p role="status">{t("ciAdminRequired")}</p><p className="muted">{t("ciCompatibility")}</p></>}
    {eligible.length > 0 && installationId && <details className="actions-ci-create" open={formOpen ?? routes.data?.length === 0} onToggle={(event) => setFormOpen(event.currentTarget.open)}>
      <summary>{t("ciNewBinding")}</summary>
      <p className="muted">{t("ciCompatibility")}</p>
      <form onSubmit={create} className="actions-ci-form" aria-busy={busy}>
      <label className="field">{t("githubRepositories")}<select name="repository" value={repository.id} onChange={(event) => { setRepositoryId(Number(event.target.value)); setBranch(null); }} required>{eligible.map((row) => <option key={row.id} value={row.id}>{row.full_name}</option>)}</select></label>
      <label className="field">Workflow<input name="workflow" defaultValue=".github/workflows/ci.yml" required pattern="(?!.*\.\.)\.github/workflows/[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,150}\.ya?ml" /></label>
      <label className="field">Job ID<input name="job" defaultValue="test" required pattern="(?!powercell_route$)[A-Za-z_][A-Za-z0-9_\-]{0,99}" /></label>
      <label className="field">Git ref<input name="ref" value={branch?.repositoryId === repository.id ? branch.value : `refs/heads/${repository.default_branch}`} onChange={(event) => setBranch({ repositoryId: repository.id, value: event.target.value })} required pattern="(?!.*\.\.)(?!.*//)(?!.*\/\.)(?!.*\.lock(?:/|$))(?!.*(?:/|\.)$)refs/heads/[A-Za-z0-9_.\/\-]{1,200}" /></label>
      <label className="field">{t("ciPool")}<select value={companyId} onChange={(event) => { setCompanyId(event.target.value); setComputerIds([]); }}><option value="">{t("personalType")}</option>{manageablePools.filter((row) => row.my_permissions.includes("execution.read")).map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
      <div className="actions-ci-computers">
        <label className="field">{t("ciComputers")}<select multiple disabled={computers.loading || !!computers.error || !powerCells.length} value={computerIds} onChange={(event) => setComputerIds(Array.from(event.target.selectedOptions, (row) => row.value))}>{powerCells.map((row) => <option key={row.id} value={row.id}>{row.name || row.id}</option>)}</select><span className="muted">{t("ciAnyComputer")}</span></label>
        {computers.loading ? <Loading /> : !computers.error && !powerCells.length && <p className="field-help">{t("ciNoComputers")}</p>}
        {computerIds.length > 0 && <button className="text-button" type="button" onClick={() => setComputerIds([])}>{t("ciClearComputers")}</button>}
      </div>
      <label className="field">CPU<input name="cpu" type="number" min="0.001" max="1000" step="0.001" defaultValue="2" required /></label>
      <label className="field">RAM · GiB<input name="memory" type="number" min="0.125" max="1048576" step="0.125" defaultValue="4" required /></label>
      <label className="field">{t("ciDisk")} · GiB<input name="disk" type="number" min="1" max="1024" defaultValue="10" required /></label>
      <label className="field">{t("ciParallel")}<input name="parallel" type="number" min="1" max="100" defaultValue="1" required /></label>
      <label className="field">{t("ciWait")}<input name="wait" type="number" min="10" max="300" defaultValue="90" required /></label>
      <p className="muted">{t("ciCapacityPolicy")}</p>
      <button className="button primary" disabled={busy || poolUnavailable || !discoveryReady}>{t("ciCreateBinding")}</button>
      </form>
    </details>}
    {routes.loading && !routes.data && <Loading />}
    {!!routes.error && <button className="text-button" onClick={routes.reload}>{t("retry")} ↻</button>}
    {!routes.loading && !routes.error && !routes.data?.length && <p className="muted">{t("ciNoBindings")}</p>}
    {!!routes.data?.length && <div className="actions-repository-list" aria-busy={routes.loading}>{routes.data.slice().sort((a, b) => Number(b.enabled) - Number(a.enabled)).map((route) => <article className={`actions-repository${selected?.id === route.id ? " is-selected" : ""}`} key={route.id}>
      <div><strong>{route.repository}</strong><p>{route.workflow_path} · {route.job_key} · {route.ref}</p><p className="muted">{route.cpu_millis / 1000} CPU · {route.memory_bytes / GiB} GiB RAM · {route.company_id ? companies.data?.find((row) => row.id === route.company_id)?.name || t("organizationType") : t("personalType")} · <span className="actions-ci-binding-state" data-enabled={route.enabled}>{route.enabled ? t("ciEnabled") : t("ciDisabled")}</span></p><p className="muted">{t("ciDisk")}: {route.disk_bytes / GiB} GiB · {t("ciWait")}: {route.preparation_seconds}</p></div>
      <div className="form-actions"><button className="button secondary compact" aria-pressed={selected?.id === route.id} aria-controls={selected ? "ci-route-details" : undefined} onClick={() => showDetails(route)}>{t("ciWorkflowAndRuns")}</button>{route.enabled && (!route.company_id || manageablePools.some((row) => row.id === route.company_id)) && <button className="button secondary compact" disabled={busy} onClick={() => disable(route.id)}>{t("ciDisable")}</button>}</div>
    </article>)}</div>}
    {selected && <div className="actions-ci-detail" id="ci-route-details" aria-busy={workflow.loading || runs.loading || routes.loading}>
      <div className="actions-section-heading">
        <h3 ref={detailHeading} tabIndex={-1}>{t("ciWorkflowAndRuns")}</h3>
        <button className="button secondary compact" disabled={workflow.loading || runs.loading || routes.loading} onClick={() => { routes.reload(); workflow.reload(); runs.reload(); savedComputers.reload(); }}>{t("refresh")}</button>
      </div>
      <p className="actions-ci-context"><strong>{selected.repository}</strong><br />{selected.workflow_path} · {selected.job_key} · {selected.ref}</p>
      <dl className="actions-ci-policy">
        <div><dt>{t("ciPool")}</dt><dd>{selected.company_id ? companies.data?.find((row) => row.id === selected.company_id)?.name || t("organizationType") : t("personalType")}</dd></div>
        <div><dt>{t("ciComputers")}</dt><dd>{selected.computer_ids.length ? selected.computer_ids.map((id) => savedComputers.data?.find((row) => row.id === id)?.name || id).join(", ") : t("ciAllComputers")}</dd></div>
        <div><dt>CPU / RAM / {t("ciDisk")}</dt><dd>{selected.cpu_millis / 1000} CPU · {selected.memory_bytes / GiB} GiB RAM · {selected.disk_bytes / GiB} GiB</dd></div>
        <div><dt>{t("ciWait")}</dt><dd>{selected.preparation_seconds}</dd></div>
        <div><dt>{t("ciParallel")}</dt><dd>{selected.max_parallel}</dd></div>
      </dl>
      {!selected.enabled && <p className="muted" role="status">{t("ciDisabledHelp")}</p>}
      <ErrorBox error={savedComputers.error} retry={savedComputers.reload} />
      <ErrorBox error={workflow.error ?? runs.error} />
      {(workflow.loading || runs.loading || routes.loading) && <Loading />}
      <h4>{t("ciRunHistory")}</h4>
      {!runs.loading && !runs.error && !runs.data?.length && <p className="muted">{t("ciNoRuns")}</p>}
      <div className="actions-ci-runs">{runs.data?.map((run) => {
        const conclusion = conclusionLabels[run.reason as keyof typeof conclusionLabels];
        return <p key={run.id}><a href={run.url} target="_blank" rel="noreferrer">#{run.run_id} / {run.run_attempt}</a><span className="actions-ci-status" data-result={conclusion ? run.reason : Object.hasOwn(statusLabels, run.status) ? run.status : undefined}>{t(conclusion ?? statusLabels[run.status as keyof typeof statusLabels] ?? "ciDetailsInGitHub")}</span>{run.reason && !conclusion && <span className="muted">{t(reasonLabels[run.reason as keyof typeof reasonLabels] ?? "ciDetailsInGitHub")}</span>}</p>;
      })}</div>
      <h4>{t(selected.enabled ? "ciWorkflowSetup" : "ciWorkflowSaved")}</h4>
      {selected.enabled && <ol className="actions-ci-steps muted"><li>{t("ciWorkflowHelp")}</li><li>{t("ciWorkflowTarget")}</li><li>{t("ciWorkflowApply")}</li></ol>}
      {workflow.data && <label className="field">Workflow<textarea aria-label="Workflow YAML" readOnly value={workflow.data.yaml} rows={14} spellCheck={false} /></label>}
      {selected.enabled && <p className="muted">{t("ciWorkflowCaveats")}</p>}
    </div>}
  </section>;
}
