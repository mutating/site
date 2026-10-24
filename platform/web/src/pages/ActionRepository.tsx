import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { allPages, api, json } from "../lib/api";
import { useData } from "../lib/useData";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import { catalogReturn, connectionLabel, runLabel, runReason } from "../lib/ci";
import { GITHUB_DISCOVERY_TIMEOUT_MS, type CIProfile, type CIRoute, type CIRun, type RepositoryConfiguration } from "../lib/github";
import { WorkflowProfiles } from "../components/WorkflowProfiles";
import type { Company, Computer } from "../lib/types";
import { ErrorBox, Heading, Loading } from "../components/UI";

const GiB = 1024 ** 3;

export function ActionRepository() {
  const { user } = useSession();
  const { repositoryId } = useParams();
  const [params] = useSearchParams();
  return <RepositoryEditor key={`${user!.id}/${repositoryId}/${params.get("installation")}`} />;
}

function RepositoryEditor() {
  const { repositoryId } = useParams();
  const [params, setParams] = useSearchParams();
  const { user } = useSession();
  const { t, locale } = useI18n();
  const [busy, setBusy] = useState(false);
  const toggling = useRef(false);
  const [error, setError] = useState<{ selection: string; failure: unknown } | null>(null);
  const [saveResult, setSaved] = useState<false | { selection: string; focusTarget: Element | null }>(false);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [refreshCount, setRefresh] = useState(0);
  const refreshMetadata = () => { setError(null); metadata.reload(); };
  const reload = () => { setSaved(false); refreshMetadata(); setRefresh(value => value + 1); };
  const installation = params.get("installation") ?? "";
  const branch = params.get("branch");
  const requestedBinding = params.get("binding")?.toLowerCase();
  const key = `${user!.id}/${repositoryId}/${installation}`;
  const metadata = useData(async signal => {
    const value = await api<RepositoryConfiguration>(`github/repositories/${repositoryId}/configuration?${new URLSearchParams({ installation_id: installation, ...(branch ? { branch } : {}), ...(params.get("binding") ? { binding_id: params.get("binding")! } : {}) })}`, { signal }, GITHUB_DISCOVERY_TIMEOUT_MS);
    return { key, binding: requestedBinding, branch: branch ?? value.bindings.find(row => row.id === value.binding_id)?.ref.replace(/^refs\/heads\//, "") ?? value.repository.default_branch, value };
  }, key, true);
  const data = metadata.data?.key === key && metadata.data.binding === requestedBinding && (!requestedBinding || metadata.data.value.bindings.some(row => row.id === requestedBinding)) ? metadata.data.value : null;
  const selection = JSON.stringify([branch, params.get("binding")]);
  const saved = saveResult && saveResult.selection === selection ? saveResult : false;
  const currentSelection = useRef(selection);
  currentSelection.current = selection;
  const lastSelection = useRef(selection);
  useEffect(() => { if (lastSelection.current !== selection) { lastSelection.current = selection; metadata.reload(); } }, [selection]);
  const companies = useData(signal => allPages<Company>("companies", signal), user!.id);
  const binding = requestedBinding ? data?.bindings.find(row => row.id === requestedBinding) : data?.bindings.find(row => row.id === data.binding_id) ?? data?.bindings.find(row => row.installation_id === data.repository.installation_id);
  const currentInstallation = !binding || binding.installation_id === data?.repository.installation_id;
  const currentBranch = branch ?? binding?.ref.replace(/^refs\/heads\//, "") ?? data?.repository.default_branch;
  const hasSetup = !!binding?.enabled && currentInstallation && (!branch || `refs/heads/${branch}` === binding.ref) && metadata.data?.branch === currentBranch;
  const historyParams = new URLSearchParams(params);
  if (binding) historyParams.set("binding", binding.id);
  const canManagePool = !binding?.company_id || !!companies.data?.some(row => row.id === binding.company_id && row.my_permissions.includes("resources.manage") && row.my_permissions.includes("execution.run"));
  const canManage = !!data?.repository.can_administer && canManagePool;
  const setBranch = (value: string) => { setSaved(false); const next = new URLSearchParams(params); next.set("branch", value); setParams(next, { replace: true }); };
  const onSaved = (value: CIRoute, focusTarget: Element | null) => {
    if (!mounted.current) return;
    if (currentSelection.current !== selection) { metadata.reload(); return; }
    setError(null);
    metadata.update(previous => {
      if (!previous) return previous;
      const existing = previous.value.bindings.find(row => row.id === value.id);
      return { ...previous, binding: value.id, value: { ...previous.value, binding_id: value.id, state: !value.enabled ? "disabled" : existing?.revision === value.revision ? previous.value.state : "pending", bindings: [{ ...existing, ...value }, ...previous.value.bindings.filter(row => row.id !== value.id)] } };
    });
    const next = new URLSearchParams(params); next.set("binding", value.id); next.set("branch", value.ref.replace(/^refs\/heads\//, "")); setParams(next, { replace: true });
    setSaved({ selection: JSON.stringify([value.ref.replace(/^refs\/heads\//, ""), value.id]), focusTarget }); metadata.reload();
  };
  async function toggle() {
    if (!binding || toggling.current) return;
    const focusTarget = document.activeElement;
    toggling.current = true; setBusy(true); setError(null); setSaved(false);
    try { onSaved(await api<CIRoute>(`github/routes/${binding.id}`, json("PATCH", { revision: binding.revision, enabled: !binding.enabled })), focusTarget); }
    catch (failure) { setError({ selection, failure }); }
    finally { toggling.current = false; setBusy(false); }
  }
  return <div className="repository-settings-page">
    <Link className="text-link" to={catalogReturn(params.get("return"))}>← {t("repositoryBack")}</Link>
    <Heading index={`03 / ${t("actionsTag")}`} title={data?.repository.full_name ?? t("repositorySettings")}>{t("repositoryCatalogLead")}</Heading>
    <ErrorBox error={metadata.error} retry={refreshMetadata} />
    {!data ? metadata.loading && <Loading /> : <>
      <div className="repository-settings-toolbar"><span className={`repository-state state-${data.state}`}>{t(connectionLabel[data.state])}</span><div className="form-actions">{hasSetup && <a className="button secondary compact" href="#workflow-setup">{t("bindingJumpToSetup")}</a>}<button className="button secondary compact" disabled={metadata.loading} onClick={reload}>{t("refresh")}</button>{data.bindings.length > 0 && <Link className="button secondary compact" to={`history?${historyParams}`}>{t("bindingHistory")}</Link>}{binding && (binding.enabled ? canManagePool : canManage && currentInstallation) && <button className="button secondary compact" disabled={busy || (!binding.enabled && binding.active_runs)} onClick={toggle}>{t(binding.enabled ? "bindingDisable" : "bindingEnable")}</button>}</div></div>
      {data.last_run && <p className="field-help"><a className="repository-run-result" data-status={data.last_run.status} data-result={data.last_run.reason ?? data.last_run.status} href={data.last_run.url} target="_blank" rel="noreferrer">{t("repositoryLastRun")}: {t(runLabel(data.last_run))} · {new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(data.last_run.created_at))}</a></p>}
      <ErrorBox error={error?.selection === selection ? error.failure : null} retry={refreshMetadata} conflict="bindingConflict" />
      {saved && <p role="status" className="binding-save-feedback">{t("bindingSaved")}</p>}
      <section className="panel repository-binding-panel"><h2>{t("repositorySettings")}</h2>
        {!data.repository.can_administer && <p role="status" className="muted">{t("ciAdminRequired")}</p>}
        {data.repository.can_administer && binding?.company_id && !companies.loading && !canManage && <p role="status" className="muted">{t("bindingPoolReadOnly")}</p>}
        {!currentInstallation && <p role="status" className="muted">{t("bindingOldInstallation")}</p>}
        {binding?.active_runs && <p role="status" className="muted">{t("bindingActiveHelp")}</p>}
        {companies.loading ? <Loading /> : companies.error ? <ErrorBox error={companies.error} retry={companies.reload} /> : <BindingForm key={binding ? `${binding.id}/${binding.revision}` : `new/${repositoryId}`} metadata={data} binding={binding} companies={companies.data ?? []} branch={branch ?? binding?.ref.replace(/^refs\/heads\//, "") ?? data.repository.default_branch} onBranch={setBranch} onSaved={onSaved} busy={busy} setBusy={setBusy} refreshCount={refreshCount} readOnly={busy || !canManage || !currentInstallation || !!binding?.active_runs || metadata.loading || !!metadata.error} />}
      </section>
      {binding && <RunHistory binding={binding} refreshCount={refreshCount} />}
      {binding?.enabled && currentInstallation && (branch && `refs/heads/${branch}` !== binding.ref ? <p role="status" className="field-help">{t("bindingSaveToSetup")}</p> : hasSetup && <WorkflowSetup key={`${binding.id}/${binding.revision}/${binding.ref}/${refreshCount}`} binding={binding} workflows={data.workflows} focusFrom={saved ? saved.focusTarget : undefined} />)}
      <p className="field-help repository-compatibility">{t("ciCompatibility")}</p>
    </>}
  </div>;
}

function BindingForm({ metadata, binding, companies, branch, onBranch, onSaved, busy, setBusy, readOnly, refreshCount }: { metadata: RepositoryConfiguration; binding?: CIRoute; companies: Company[]; branch: string; onBranch: (value: string) => void; onSaved: (value: CIRoute, focusTarget: Element | null) => void; busy: boolean; setBusy: (value: boolean) => void; readOnly: boolean; refreshCount: number }) {
  const { t } = useI18n();
  const { user } = useSession();
  const [company, setCompany] = useState(binding?.company_id ?? "");
  const [computers, setComputers] = useState(binding?.computer_ids ?? []);
  const [profiles, setProfiles] = useState(binding?.workflow_profiles ?? {});
  const [defaults, setDefaults] = useState<CIProfile>({ cpu_millis: binding?.cpu_millis ?? 2000, memory_bytes: binding?.memory_bytes ?? 4 * GiB, disk_bytes: binding?.disk_bytes ?? 10 * GiB, preparation_seconds: binding?.preparation_seconds ?? 300, timeout_seconds: binding?.timeout_seconds ?? 3600, computer_ids: computers });
  const [paths, setPaths] = useState(binding?.workflow_paths ?? (binding ? [binding.workflow_path] : []));
  const [allWorkflows, setAllWorkflows] = useState(!binding || binding.workflow_paths?.length === 0);
  const [error, setError] = useState<unknown>(null);
  const inFlight = useRef(false);
  const canReadComputers = !company || !!companies.find(row => row.id === company)?.my_permissions.includes("resources.read");
  const cells = useData(signal => canReadComputers ? allPages<Computer>(`computers?${company ? `company_id=${company}` : `user_id=${user!.id}`}`, signal) : Promise.resolve([]), `${user!.id}/${company}/${canReadComputers}/${refreshCount}`);
  const options = cells.data?.filter(row => row.runtime === "powercell-ubuntu-arm64-v1") ?? [];
  const inventoryUnavailable = !canReadComputers || cells.loading || !!cells.error;
  const unavailableComputers = inventoryUnavailable ? [] : [...computers, ...Object.values(profiles).flatMap(profile => profile.computer_ids ?? [])].filter(id => !options.some(row => row.id === id));
  const pools = companies.filter(row => row.my_permissions.includes("resources.manage") && row.my_permissions.includes("execution.run") && row.my_permissions.includes("execution.read"));
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (inFlight.current || readOnly || cells.loading || cells.error || unavailableComputers.length || (!allWorkflows && !paths.length)) return;
    const focusTarget = document.activeElement;
    const form = new FormData(event.currentTarget);
    const legacy = binding?.workflow_paths == null && !allWorkflows && paths.length === 1 && paths[0] === binding?.workflow_path;
    const configuration = {
      installation_id: metadata.repository.installation_id, repository_id: metadata.repository.id,
      workflow_paths: legacy ? null : allWorkflows ? [] : paths,
      ...(legacy ? { workflow_path: binding!.workflow_path, job_key: binding!.job_key } : {}),
      ref: `refs/heads/${branch}`, company_id: company || null, computer_ids: computers, workflow_profiles: profiles,
      cpu_millis: Math.round(Number(form.get("cpu")) * 1000), memory_bytes: Math.round(Number(form.get("memory")) * GiB), disk_bytes: Math.round(Number(form.get("disk")) * GiB), max_parallel: Number(form.get("parallel")), preparation_seconds: Number(form.get("wait")), timeout_seconds: Math.round(Number(form.get("duration")) * 60),
    };
    inFlight.current = true; setBusy(true); setError(null);
    try { onSaved(await api<CIRoute>(binding ? `github/routes/${binding.id}` : "github/routes", json(binding ? "PATCH" : "POST", binding ? { revision: binding.revision, configuration } : configuration)), focusTarget); }
    catch (failure) { setError(failure); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return <form onSubmit={submit} onInvalidCapture={event => {
    const firstInvalid = event.currentTarget.querySelector<HTMLInputElement>("input:invalid, select:invalid");
    if (!firstInvalid || event.target !== firstInvalid) return;
    const details = firstInvalid.closest("details");
    if (details) details.open = true;
    requestAnimationFrame(() => {
      firstInvalid.focus({ preventScroll: true });
      firstInvalid.scrollIntoView({ block: "center" });
    });
  }} onChange={event => { const form = new FormData(event.currentTarget); setDefaults(previous => ({ ...previous, cpu_millis: Math.round(Number(form.get("cpu")) * 1000), memory_bytes: Math.round(Number(form.get("memory")) * GiB), disk_bytes: Math.round(Number(form.get("disk")) * GiB), preparation_seconds: Number(form.get("wait")), timeout_seconds: Math.round(Number(form.get("duration")) * 60) })); }} aria-busy={busy} className="repository-binding-form">
    <fieldset disabled={busy || readOnly}>
      <fieldset className="binding-workflow-selection"><legend>{t("bindingWorkflows")}</legend><label className="binding-check"><input type="checkbox" checked={allWorkflows} onChange={event => setAllWorkflows(event.target.checked)} />{t("bindingAllWorkflows")}</label><p className="field-help">{t("bindingWorkflowsHelp")}</p>
        {!allWorkflows && <div className="binding-workflow-options">{Array.from(new Set([...metadata.workflows.map(row => row.path), ...paths])).map(path => <label className="binding-check" key={path}><input type="checkbox" checked={paths.includes(path)} onChange={event => setPaths(previous => event.target.checked ? [...previous, path] : previous.filter(value => value !== path))} />{path}</label>)}</div>}
      </fieldset>
      <div className="binding-form-grid">
        <label className="field">{t("bindingBranches")}<select aria-label={t("bindingBranches")} value={branch} onChange={event => onBranch(event.target.value)}>{Array.from(new Set([branch, ...metadata.branches])).map(name => <option key={name}>{name}</option>)}</select></label>
        <label className="field">{t("ciPool")}<select aria-label={t("ciPool")} value={company} onChange={event => { setCompany(event.target.value); setComputers([]); setProfiles(previous => Object.fromEntries(Object.entries(previous).map(([path, profile]) => [path, profile.computer_ids === undefined ? profile : { ...profile, computer_ids: [] }]))); }}><option value="">{t("personalType")}</option>{Array.from(new Set([...pools.map(row => row.id), ...(company ? [company] : [])])).map(id => <option key={id} value={id} disabled={!pools.some(row => row.id === id)}>{companies.find(row => row.id === id)?.name ?? id}</option>)}</select></label>
      </div><p className="field-help">{t("bindingPoolHelp")}</p>
      <label className="field">{t("bindingParallel")}<input name="parallel" type="number" min="1" max="100" defaultValue={binding?.max_parallel ?? 1} required /></label><p className="field-help">{t("bindingParallelHelp")}</p>
      <h3>{t("bindingDefaultProfile")}</h3><p className="field-help">{t("bindingDefaultProfileHelp")}</p><div className="binding-form-grid binding-resource-grid">
        <label className="field">CPU<input name="cpu" type="number" min="0.001" max="1000" step="0.001" defaultValue={(binding?.cpu_millis ?? 2000) / 1000} required /></label>
        <label className="field">RAM · GiB<input name="memory" type="number" min="0.015625" max="1048576" step="any" defaultValue={(binding?.memory_bytes ?? 4 * GiB) / GiB} required /></label>
        <label className="field">{t("ciDisk")} · GiB<input name="disk" type="number" min="1" max="1024" step="any" defaultValue={(binding?.disk_bytes ?? 10 * GiB) / GiB} required /></label>
      </div>
      <ErrorBox error={cells.error} retry={cells.reload} />
      {!cells.loading && !cells.error && unavailableComputers.length > 0 && <p role="status" className="field-help">{t("bindingComputersChanged")}</p>}
      <details className="binding-advanced"><summary>{t("bindingAdvanced")}</summary>
        <label className="field">{t("ciComputers")}<select aria-label={t("ciComputers")}  multiple value={computers} disabled={inventoryUnavailable || !options.length} onChange={event => setComputers(Array.from(event.target.selectedOptions, row => row.value))}>{options.map(row => <option value={row.id} key={row.id}>{row.name || row.id}</option>)}{computers.filter(id => !options.some(row => row.id === id)).map(id => <option key={id} value={id} disabled>{id} · {t(inventoryUnavailable ? "bindingComputerPreserved" : "bindingComputerUnavailable")}</option>)}</select><small>{t("ciAnyComputer")}</small></label>
        {canReadComputers && !cells.loading && !cells.error && !options.length && <p className="field-help">{t("ciNoComputers")}</p>}
        {computers.length > 0 && <button type="button" className="text-button" onClick={() => setComputers([])}>{t("ciClearComputers")}</button>}
        <div className="binding-form-grid"><label className="field">{t("bindingPreparation")}<input aria-label={t("bindingPreparation")} name="wait" type="number" min="10" max="300" defaultValue={binding?.preparation_seconds ?? 300} required /><small>{t("bindingPreparationHelp")}</small></label><label className="field">{t("bindingDuration")}<input name="duration" type="number" min="1" max="360" step="any" defaultValue={(binding?.timeout_seconds ?? 3600) / 60} required /></label></div>
      </details><p className="field-help">{t("ciCapacityPolicy")}</p>
      <WorkflowProfiles profiles={profiles} onChange={setProfiles} defaults={{ ...defaults, computer_ids: computers }} workflows={metadata.workflows.filter(row => allWorkflows || paths.includes(row.path))} computers={options} inventoryReadable={canReadComputers} inventoryUnavailable={inventoryUnavailable} />
      <ErrorBox error={error} conflict="bindingConflict" />
      <button className="button primary" disabled={busy || cells.loading || !!cells.error || !!unavailableComputers.length || (!allWorkflows && !paths.length)}>{t(busy ? "waiting" : binding ? "bindingSave" : "repositoryConnect")}</button>
    </fieldset>
  </form>;
}

function WorkflowSetup({ binding, workflows, focusFrom }: { binding: CIRoute; workflows: RepositoryConfiguration["workflows"]; focusFrom?: Element | null }) {
  const { t } = useI18n();
  const allowed = workflows.filter(row => binding.workflow_paths == null ? row.path === binding.workflow_path : !binding.workflow_paths.length || binding.workflow_paths.includes(row.path));
  const [chosenPath, setPath] = useState(allowed[0]?.path ?? "");
  const workflow = allowed.find(row => row.path === chosenPath) ?? allowed[0];
  const [chosenJob, setJob] = useState(workflow?.jobs.includes(binding.job_key) ? binding.job_key : workflow?.jobs[0] ?? "");
  const job = workflow?.jobs.includes(chosenJob) ? chosenJob : workflow?.jobs[0] ?? "";
  const profile = { ...binding, ...binding.workflow_profiles?.[workflow?.path ?? ""] };
  const text = useRef<HTMLTextAreaElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (focusFrom !== undefined && (document.activeElement === focusFrom || document.activeElement === document.body)) { heading.current?.focus({ preventScroll: true }); heading.current?.scrollIntoView({ block: "start" }); } }, [focusFrom]);
  const [copyState, setCopy] = useState<{ key: string; state: "bindingCopied" | "bindingCopyFallback" } | null>(null);
  const snippetKey = `${binding.id}/${binding.revision}/${workflow?.path}/${job}`;
  const currentSnippet = useRef(snippetKey);
  currentSnippet.current = snippetKey;
  const snippet = useData(signal => workflow && job ? api<{ yaml: string }>(`github/routes/${binding.id}/workflow?${new URLSearchParams({ workflow_path: workflow.path, job_key: job })}`, { signal }) : Promise.resolve(null), snippetKey);
  return <section className="panel repository-yaml"><h2 id="workflow-setup" ref={heading} tabIndex={-1}>{t("bindingSetup")}</h2><p className="muted">{t("bindingSetupHelp")}</p>
    <ol className="actions-ci-steps muted"><li>{t("ciWorkflowHelp")}</li><li>{t("ciWorkflowTarget")}</li><li>{t("ciWorkflowApply")}</li></ol>
    <details><summary>{t("bindingAdvanced")}</summary><p className="field-help">{t("ciWorkflowCaveats")}</p></details>
    {!workflow ? <p role="status">{t("bindingNoWorkflows")}</p> : <>
      <div className="binding-form-grid"><label className="field">{t("bindingWorkflow")}<select aria-label={t("bindingWorkflow")} value={workflow.path} onChange={event => { setPath(event.target.value); setJob(""); setCopy(null); }}>{allowed.map(row => <option key={row.path} value={row.path}>{row.name} · {row.path}</option>)}</select></label><label className="field">{t("bindingJob")}<select aria-label={t("bindingJob")} value={job} disabled={!job} onChange={event => { setJob(event.target.value); setCopy(null); }}>{workflow.jobs.map(name => <option key={name}>{name}</option>)}</select></label></div>
      {!job ? <p role="status">{t("bindingWorkflowNoJobs")}</p> : <>
      <p className="field-help">{t("bindingJobHelp")}</p><p className="field-help">{t("bindingProfileApplied")}: {binding.workflow_profiles?.[workflow.path] ? workflow.path : t("bindingDefaultProfile")} · {profile.cpu_millis / 1000} CPU · {profile.memory_bytes / GiB} GiB RAM · {profile.disk_bytes / GiB} GiB</p><ErrorBox error={snippet.error} retry={snippet.reload} />
      {snippet.loading ? <Loading /> : snippet.data && <><label className="field">Workflow YAML<textarea aria-label="Workflow YAML" ref={text} readOnly rows={14} value={snippet.data.yaml} spellCheck={false} /></label><button className="button secondary" onClick={async () => {
        const target = text.current;
        try {
          await navigator.clipboard.writeText(snippet.data!.yaml);
          if (currentSnippet.current === snippetKey && text.current === target) setCopy({ key: snippetKey, state: "bindingCopied" });
        } catch {
          if (currentSnippet.current === snippetKey && text.current === target) { target?.select(); setCopy({ key: snippetKey, state: "bindingCopyFallback" }); }
        }
      }}>{t("bindingCopyYaml")}</button><p role="status">{copyState?.key === snippetKey ? t(copyState.state) : ""}</p></>}
      </>}
    </>}
  </section>;
}

function RunHistory({ binding, refreshCount }: { binding: CIRoute; refreshCount: number }) {
  const { t } = useI18n();
  const runs = useData(signal => allPages<CIRun>(`github/routes/${binding.id}/runs`, signal, 100), `${binding.id}/${binding.revision}/${refreshCount}`, true);
  return <section className="panel repository-runs"><h2>{t("ciRunHistory")}</h2><ErrorBox error={runs.error} retry={runs.reload} />{runs.loading && !runs.data ? <Loading /> : runs.data && <>{!runs.data.length && <p className="field-help">{t("ciNoRuns")}</p>}<div className="actions-ci-runs">{runs.data.map(run => <p key={run.id}><a href={run.url} target="_blank" rel="noreferrer">#{run.run_id} / {run.run_attempt}</a><span className="actions-ci-status" data-status={run.status} data-result={run.reason ?? run.status}>{t(runLabel(run))}</span>{runReason(run.reason) && <span className="muted">{t(runReason(run.reason)!)}</span>}</p>)}</div></> }</section>;
}
