import { useState, type FormEvent } from "react";
import { allPages, api, json } from "../lib/api";
import { useData } from "../lib/useData";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import type { Company, Computer } from "../lib/types";
import type { CIRoute, CIRun, GitHubRepository } from "../lib/github";
import { ErrorBox, Loading } from "./UI";

const GiB = 1024 ** 3;
const statusLabels = { preparing: "ciPreparing", ready: "ciSelected", selected: "ciSelected", running: "ciRunning", completed: "ciCompleted", cancelled: "ciCancelled", timed_out: "ciTimedOut", fallback: "ciFallback", failed: "ciFailed" } as const;
const reasonLabels = { no_capacity: "ciNoCapacity", concurrency_limit: "ciConcurrencyLimit", isolate_quota: "ciIsolateQuota", route_disabled: "ciRouteDisabled", authorization_revoked: "ciAuthorizationChanged", github_unavailable: "ciGitHubUnavailable", preparation_timeout: "ciPreparationTimeout", bootstrap_cancelled: "ciFallback", runner_timeout: "ciTimedOut", isolate_unavailable: "ciIsolateUnavailable" } as const;

export function CIRoutes({ installationId, repositories }: { installationId?: number; repositories: GitHubRepository[] }) {
  const { user } = useSession();
  const { t } = useI18n();
  const [companyId, setCompanyId] = useState("");
  const [computerIds, setComputerIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const routes = useData((signal) => allPages<CIRoute>("github/routes", signal), user!.id);
  const companies = useData((signal) => allPages<Company>("companies", signal), user!.id);
  const computers = useData((signal) => allPages<Computer>(`computers?${companyId ? `company_id=${companyId}` : `user_id=${user!.id}`}`, signal), `${user!.id}/${companyId}`);
  const workflow = useData((signal) => selected ? api<{ yaml: string }>(`github/routes/${selected}/workflow`, { signal }) : Promise.resolve(null), selected ?? "");
  const runs = useData((signal) => selected ? api<CIRun[]>(`github/routes/${selected}/runs`, { signal }) : Promise.resolve([]), selected ?? "");
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
        fallback: form.get("fallback"),
      }));
      routes.reload();
      setSelected(route.id);
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
      routes.reload();
      runs.reload();
    } catch (error) { setError(error); }
    finally { setBusy(false); }
  }
  const eligible = repositories.filter((row) => row.can_administer);
  return <section className="panel actions-repositories">
    <h2>{t("ciBindings")}</h2>
    <p className="muted">{t("ciCompatibility")}</p>
    <ErrorBox error={error ?? routes.error ?? companies.error ?? computers.error} />
    {eligible.length > 0 && installationId && <form onSubmit={create} className="actions-ci-form" aria-busy={busy}>
      <label className="field">{t("githubRepositories")}<select name="repository" required>{eligible.map((row) => <option key={row.id} value={row.id}>{row.full_name}</option>)}</select></label>
      <label className="field">Workflow<input name="workflow" defaultValue=".github/workflows/ci.yml" required pattern="\.github/workflows/[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,150}\.ya?ml" /></label>
      <label className="field">Job ID<input name="job" defaultValue="test" required pattern="[A-Za-z_][A-Za-z0-9_\-]{0,99}" /></label>
      <label className="field">Git ref<input name="ref" defaultValue="refs/heads/main" required /></label>
      <label className="field">{t("ciPool")}<select value={companyId} onChange={(event) => { setCompanyId(event.target.value); setComputerIds([]); }}><option value="">{t("personalType")}</option>{companies.data?.filter((row) => row.my_permissions.includes("resources.manage") && row.my_permissions.includes("execution.run")).map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
      <label className="field">{t("ciComputers")}<select multiple value={computerIds} onChange={(event) => setComputerIds(Array.from(event.target.selectedOptions, (row) => row.value))}>{computers.data?.filter((row) => row.runtime === "powercell-ubuntu-arm64-v1").map((row) => <option key={row.id} value={row.id}>{row.name || row.id}</option>)}</select><span className="muted">{t("ciAnyComputer")}</span></label>
      <label className="field">CPU<input name="cpu" type="number" min="0.001" step="0.001" defaultValue="2" required /></label>
      <label className="field">RAM · GiB<input name="memory" type="number" min="0.125" step="0.125" defaultValue="4" required /></label>
      <label className="field">{t("ciDisk")} · GiB<input name="disk" type="number" min="1" defaultValue="10" required /></label>
      <label className="field">{t("ciParallel")}<input name="parallel" type="number" min="1" max="100" defaultValue="1" required /></label>
      <label className="field">{t("ciWait")}<input name="wait" type="number" min="10" max="180" defaultValue="90" required /></label>
      <label className="field">Fallback<select name="fallback"><option value="ubuntu-24.04-arm">GitHub · Ubuntu 24.04 ARM64</option><option value="none">{t("ciNoFallback")}</option></select></label>
      <p className="muted">{t("ciFallbackLimits")}</p>
      <button className="button" disabled={busy}>{t("ciCreateBinding")}</button>
    </form>}
    {routes.loading ? <Loading /> : routes.error ? <button className="text-button" onClick={routes.reload}>{t("retry")} ↻</button> : !routes.data?.length ? <p className="muted">{t("ciNoBindings")}</p> : <div className="actions-repository-list">{routes.data.map((route) => <article className="actions-repository" key={route.id}>
      <div><strong>{route.repository}</strong><p>{route.workflow_path} · {route.job_key}</p><p className="muted">{route.cpu_millis / 1000} CPU · {route.memory_bytes / GiB} GiB RAM · {route.company_id ? companies.data?.find((row) => row.id === route.company_id)?.name || t("organizationType") : t("personalType")} · {route.enabled ? t("ciEnabled") : t("ciDisabled")}</p></div>
      <div className="form-actions"><button className="button secondary compact" onClick={() => setSelected(route.id)}>{t("ciWorkflowAndRuns")}</button>{route.enabled && <button className="button secondary compact" disabled={busy} onClick={() => disable(route.id)}>{t("ciDisable")}</button>}</div>
    </article>)}</div>}
    {selected && <div className="actions-ci-detail">
      <h3>{t("ciWorkflowAndRuns")}</h3><p className="muted">{t("ciWorkflowHelp")}</p>
      <ErrorBox error={workflow.error ?? runs.error} />
      {workflow.data && <label className="field">Workflow<textarea aria-label="Workflow YAML" readOnly value={workflow.data.yaml} rows={14} spellCheck={false} /></label>}
      <button className="button secondary compact" onClick={() => { workflow.reload(); runs.reload(); }}>{t("refresh")}</button>
      {!runs.loading && !runs.error && !runs.data?.length && <p className="muted">{t("ciNoRuns")}</p>}
      {runs.data?.map((run) => <p key={run.id}><a href={run.url} target="_blank" rel="noreferrer">#{run.run_id} / {run.run_attempt}</a> · {t(statusLabels[run.status as keyof typeof statusLabels] ?? "ciDetailsInGitHub")}{run.reason ? ` · ${t(reasonLabels[run.reason as keyof typeof reasonLabels] ?? "ciDetailsInGitHub")}` : ""}</p>)}
    </div>}
  </section>;
}
