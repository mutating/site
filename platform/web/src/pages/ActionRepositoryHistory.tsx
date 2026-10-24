import { useEffect } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { allPages, api } from "../lib/api";
import { useData } from "../lib/useData";
import { useI18n, type MessageKey } from "../lib/i18n";
import { useSession } from "../lib/session";
import { catalogReturn } from "../lib/ci";
import { memberName } from "../lib/permissions";
import type { BindingRevision, CIRoute } from "../lib/github";
import type { Company } from "../lib/types";
import { ErrorBox, Heading, Loading } from "../components/UI";

const labels: Record<string, MessageKey> = {
  repository: "bindingRepository", workflow_path: "bindingWorkflow", workflow_paths: "bindingWorkflows", workflow_profiles: "bindingProfiles", ref: "bindingBranches", job_key: "bindingJob", company_id: "ciPool", computer_ids: "ciComputers", cpu_millis: "bindingCPU", memory_bytes: "bindingRAM", disk_bytes: "ciDisk", preparation_seconds: "bindingPreparation", timeout_seconds: "bindingDuration", max_parallel: "bindingParallel", enabled: "repositoryState",
};
const actions: Record<string, MessageKey> = { created: "bindingCreated", updated: "bindingUpdated", disabled: "bindingDisabled", enabled: "bindingEnabled", disconnected: "bindingDisconnected", imported: "bindingImported", reauthorized: "bindingReauthorized" };

export function ActionRepositoryHistory() {
  const { repositoryId } = useParams();
  const [params, setParams] = useSearchParams();
  const { user } = useSession();
  const { t, locale } = useI18n();
  const bindings = useData(async signal => (await allPages<CIRoute>("github/routes", signal)).filter(row => String(row.repository_id) === repositoryId).sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id)), `${user!.id}/${repositoryId}`);
  const companies = useData(signal => allPages<Company>("companies", signal), user!.id);
  const selected = params.get("binding")?.toLowerCase() ?? null;
  const binding = selected === null ? bindings.data?.[0] : bindings.data?.find(row => row.id === selected);
  const page = Math.max(1, Math.floor(Number(params.get("page"))) || 1);
  const history = useData(signal => binding ? api<{ total_count: number; revisions: BindingRevision[] }>(`github/routes/${binding.id}/revisions?limit=20&offset=${(page - 1) * 20}`, { signal }) : Promise.resolve(null), `${user!.id}/${binding?.id}/${page}`);
  const pages = Math.max(1, Math.ceil((history.data?.total_count ?? 0) / 20));
  const change = (name: string, value: string) => { const next = new URLSearchParams(params); next.set(name, value); if (name !== "page") next.delete("page"); if (name === "binding") next.delete("branch"); setParams(next); };
  useEffect(() => { if (history.data && page > pages) change("page", String(pages)); }, [history.data, page, pages]);
  const back = new URLSearchParams(params); back.delete("page");
  if (binding) back.set("binding", binding.id);
  const value = (field: string, data: unknown): string => {
    if (data == null) return "—";
    if (field === "enabled") return t(data ? "ciEnabled" : "ciDisabled");
    if (field === "workflow_paths") return Array.isArray(data) && !data.length ? t("bindingAllWorkflows") : (data as string[]).join(", ");
    if (field === "company_id") return companies.data?.find(row => row.id === data)?.name ?? String(data);
    if (field === "ref") return String(data).replace(/^refs\/heads\//, "");
    if (field === "cpu_millis") return `${Number(data) / 1000} CPU`;
    if (field === "memory_bytes" || field === "disk_bytes") return `${Number(data) / 1024 ** 3} GiB`;
    if (field === "timeout_seconds") return String(Number(data) / 60);
    if (Array.isArray(data)) return data.length ? data.join(", ") : t("ciAllComputers");
    return String(data);
  };
  const display = (field: string, data: unknown) => {
    if (field !== "workflow_profiles" || data == null) return value(field, data);
    const profiles = Object.entries(data as Record<string, Record<string, unknown>>);
    if (!profiles.length) return t("bindingDefaultProfile");
    return <ul className="binding-profile-diff">{profiles.map(([path, profile]) => <li key={path}><strong>{path}:</strong>{" "}{Object.keys(profile).length ? <ul>{Object.entries(profile).map(([name, setting]) => <li key={name}>{t(labels[name])}: {value(name, setting)}</li>)}</ul> : t("bindingDefaultProfile")}</li>)}</ul>;
  };
  return <div className="repository-history-page">
    <Link className="text-link" to={binding ? `/actions/repositories/${repositoryId}?${back}` : catalogReturn(params.get("return"))}>← {t(binding ? "repositorySettings" : "repositoryBack")}</Link>
    <Heading index={`03 / ${t("bindingHistory")}`} title={binding?.repository ?? t("bindingHistory")}>{t("bindingHistoryLead")}</Heading>
    <ErrorBox error={bindings.error} retry={bindings.reload} />
    {bindings.loading ? <Loading /> : bindings.error ? null : !binding ? <p role="status">{t(selected === null ? "bindingHistoryEmpty" : "bindingHistoryUnavailable")}</p> : <>
      <label className="field">{t("bindingHistorySelect")}<select aria-label={t("bindingHistorySelect")} value={binding.id} onChange={event => change("binding", event.target.value)}>{bindings.data!.map(row => <option key={row.id} value={row.id}>{row.company_id ? companies.data?.find(company => company.id === row.company_id)?.name ?? t("organizationType") : t("personalType")} · {new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(row.created_at))} · {t(row.enabled ? "ciEnabled" : "ciDisabled")} · {row.id.slice(0, 8)}{row.workflow_paths == null ? ` · ${row.workflow_path} / ${row.job_key}` : ""}</option>)}</select></label>
      <ErrorBox error={companies.error} retry={companies.reload} /><ErrorBox error={history.error} retry={history.reload} />
      {history.loading ? <Loading /> : history.data && <>
        {!history.data.revisions.length && <p>{t("bindingHistoryEmpty")}</p>}
        <ol className="binding-revision-list">{history.data.revisions.map(revision => <li className="panel binding-revision" key={revision.id}>
          <div className="actions-section-heading"><h2>{t(actions[revision.action] ?? "bindingUpdated")}</h2><span className="field-help">{t("bindingVersion")} {revision.revision}</span></div>
          <p className="field-help"><time dateTime={revision.created_at}>{new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(revision.created_at))}</time>{revision.actor && ` · ${memberName(revision.actor)}`}</p>
          <dl className="binding-changes">{(revision.changes ?? Object.entries(revision.configuration).map(([field, after]) => ({ field, before: null, after }))).filter(change => labels[change.field]).map(change => <div key={change.field}><dt>{t(labels[change.field])}</dt><dd><small>{t("bindingBefore")}</small>{change.field === "company_id" && change.before == null && revision.revision > 1 ? t("personalType") : display(change.field, change.before)}</dd><dd><small>{t("bindingAfter")}</small>{change.field === "company_id" && change.after == null ? t("personalType") : display(change.field, change.after)}</dd></div>)}</dl>
        </li>)}</ol>
        {pages > 1 && <nav className="pagination" aria-label={t("page")}><span>{t("page")} {page} {t("of")} {pages}</span><div><button className="button secondary compact" disabled={page === 1} onClick={() => change("page", String(page - 1))}>← {t("previous")}</button><button className="button secondary compact" disabled={page >= pages} onClick={() => change("page", String(page + 1))}>{t("next")} →</button></div></nav>}
      </>}
    </>}
  </div>;
}
