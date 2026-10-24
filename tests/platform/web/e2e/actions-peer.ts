import type { Page, Route } from "@playwright/test";
import { API } from "./fixtures";

export const repository = { id: 789, installation_id: 456, full_name: "mutating/ci-sandbox", owner_login: "mutating", owner_type: "organization", private: true, default_branch: "main", can_administer: true, updated_at: "2026-10-08T12:00:00Z" };
export const binding = {
  id: "11111111-1111-4111-8111-111111111111", repository: repository.full_name,
  installation_id: 456, repository_id: 789, workflow_path: ".github/workflows/ci.yml", workflow_paths: [] as string[] | null, job_key: "test", ref: "refs/heads/main",
  company_id: null, computer_ids: [], cpu_millis: 2000, memory_bytes: 4 * 1024 ** 3,
  disk_bytes: 10 * 1024 ** 3, max_parallel: 1, preparation_seconds: 300, timeout_seconds: 3600,
  enabled: true, revision: 1, created_at: "2026-10-08T12:00:00Z", active_runs: false, workflow_profiles: {} as Record<string, Record<string, any>>,
};
export const repositoryURL = `/actions/repositories/789?installation=456`;
type Row = Record<string, any>;

/** Stateful external HTTP peer; real app/session navigation and React state stay intact. */
export async function actionsPeer(page: Page) {
  const peer = {
    connected: true, enabled: true,
    bindings: [] as Row[], repositories: [structuredClone(repository)] as Row[],
    companies: [] as Row[], computers: [] as Row[], runs: [] as Row[],
    branches: ["main", "develop"],
    workflows: [{ path: ".github/workflows/ci.yml", name: "CI", jobs: ["test", "checks"], parse_error: false }, { path: ".github/workflows/deploy.yml", name: "Deploy", jobs: ["deploy"], parse_error: false }, { path: ".github/workflows/tests.yml", name: "Tests", jobs: ["api", "web"], parse_error: false }],
    history: new Map<string, Row[]>(),
    calls: [] as { method: string; path: string; query: URLSearchParams; body: Row | null }[],
    failures: new Map<string, number[]>(),
    custom: null as ((route: Route, url: URL) => Promise<boolean>) | null,
  };
  const headers = { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" };
  function summary(repo: Row, selectedId?: string | null) {
    const routes = peer.bindings.filter(row => row.repository_id === repo.id).sort((a, b) => Number(b.enabled) - Number(a.enabled));
    const selected = selectedId ? routes.filter(row => row.id === selectedId) : routes;
    const active = selected.filter(row => row.enabled);
    const last = peer.runs.filter(row => selected.length && (!row.route_id || selected.some(binding => binding.id === row.route_id))).at(-1);
    const confirmed = active.some(binding => peer.runs.some(run => (!run.route_id || run.route_id === binding.id) && run.started_at && (run.route_revision ?? 1) === binding.revision));
    return { state: (!selectedId ? repo.state : undefined) ?? (active.length ? confirmed ? "connected" : "pending" : selected.length ? "disabled" : "unconfigured"), binding_id: selected[0]?.id ?? null, bindings: routes, last_run: last ? { created_at: "2026-10-08T12:00:00Z", ...last } : null, active_bindings: active.length };
  }
  function record(row: Row, action: string) {
    const previous = peer.history.get(row.id) ?? [];
    const before = previous[0]?.configuration ?? {};
    const configuration = structuredClone(row);
    if (row.workflow_paths != null) { delete configuration.workflow_path; delete configuration.job_key; }
    previous.unshift({ id: `${row.id}-${row.revision}`, revision: row.revision, action, created_at: "2026-10-08T12:00:00Z", actor: { id: "actor", first_name: "Алиса", last_name: "Волкова", nickname: "alice", avatar_version: null }, configuration, changes: Object.entries(configuration).filter(([name, value]) => JSON.stringify(before[name]) !== JSON.stringify(value)).map(([field, after]) => ({ field, before: before[field] ?? null, after })) });
    peer.history.set(row.id, previous);
  }
  async function handle(route: Route) {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/v1/", "");
    const method = route.request().method();
    if (method === "OPTIONS") { await route.fulfill({ status: 204, headers: { ...headers, "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS", "Access-Control-Allow-Headers": "content-type,x-requested-with" } }); return; }
    const body = route.request().postData() ? route.request().postDataJSON() : null;
    peer.calls.push({ method, path, query: url.searchParams, body });
    const failure = peer.failures.get(`${method} ${path}`)?.shift();
    if (failure) { await route.fulfill({ status: failure, headers, json: { detail: "Temporary peer failure" } }); return; }
    if (peer.custom && await peer.custom(route, url)) return;
    let value: unknown;
    if (path === "github/status") value = { enabled: peer.enabled, account: peer.connected ? { id: 123, login: "github-user" } : null };
    else if (path === "github/authorize" || path === "github/installations/authorize") value = { url: "https://github.com/login/oauth/authorize?state=test-state" };
    else if (path === "github/connection") {
      peer.connected = false;
      peer.bindings.filter(row => row.enabled).forEach(row => { row.enabled = false; row.revision = (row.revision ?? 1) + 1; record(row, "disconnected"); });
      await route.fulfill({ status: 204, headers }); return;
    } else if (path === "github/repositories") {
      const owners = [...new Set(peer.repositories.map(row => row.owner_login ?? row.full_name.split("/")[0]))].sort();
      const rows = peer.repositories.map(row => ({ ...repository, ...row, ...summary(row) })).filter(row => (!url.searchParams.get("search") || row.full_name.toLowerCase().includes(url.searchParams.get("search")!.trim().toLowerCase())) && (!url.searchParams.get("owner_type") || url.searchParams.get("owner_type") === "all" || row.owner_type === url.searchParams.get("owner_type")) && (!url.searchParams.get("owner") || row.owner_login === url.searchParams.get("owner")) && (!url.searchParams.get("state") || url.searchParams.get("state") === "all" || row.state === url.searchParams.get("state"))).sort((a, b) => a.full_name.localeCompare(b.full_name));
      const sort = url.searchParams.get("sort");
      if (sort === "name_desc") rows.reverse();
      if (sort === "updated") rows.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
      if (sort === "last_run") rows.sort((a, b) => (b.last_run?.created_at ?? "").localeCompare(a.last_run?.created_at ?? ""));
      const size = Number(url.searchParams.get("per_page") ?? 20), start = (Number(url.searchParams.get("page") ?? 1) - 1) * size;
      value = { total_count: rows.length, owners, repositories: rows.slice(start, start + size) };
    } else if (/^github\/repositories\/\d+\/configuration$/.test(path)) {
      const repo = peer.repositories.find(row => String(row.id) === path.split("/")[2]);
      if (!repo) { await route.fulfill({ status: 404, headers, json: {} }); return; }
      value = { repository: { ...repository, ...repo }, branches: peer.branches, workflows: peer.workflows, ...summary(repo, url.searchParams.get("binding_id")), binding_id: url.searchParams.get("binding_id") ?? peer.bindings.find(row => row.repository_id === repo.id && row.installation_id === repo.installation_id)?.id ?? null };
    } else if (path === "github/routes" && method === "POST") {
      const row = { ...binding, ...body, repository: peer.repositories.find(row => row.id === body.repository_id)?.full_name ?? binding.repository, revision: 1 };
      peer.bindings.push(row); record(row, "created");
      await route.fulfill({ status: 201, headers, json: row }); return;
    } else if (path === "github/routes") value = peer.bindings;
    else if (path.endsWith("/revisions")) {
      const rows = peer.history.get(path.split("/")[2]) ?? [];
      const offset = Number(url.searchParams.get("offset") ?? 0), limit = Number(url.searchParams.get("limit") ?? 20);
      value = { total_count: rows.length, revisions: rows.slice(offset, offset + limit) };
    } else if (path.endsWith("/workflow")) value = { yaml: `# ${path}\n# ${url.searchParams.get("workflow_path")}\njobs:\n  ${url.searchParams.get("job_key") ?? "test"}:\n    needs: powercell_route\n` };
    else if (path.endsWith("/runs")) { const offset = Number(url.searchParams.get("offset") ?? 0), limit = Number(url.searchParams.get("limit") ?? 50); if (limit > 100) { await route.fulfill({ status: 422, headers, json: {} }); return; } value = peer.runs.filter(row => !row.route_id || row.route_id === path.split("/")[2]).slice(offset, offset + limit); }
    else if ((method === "PATCH" || method === "DELETE") && path.startsWith("github/routes/")) {
      const row = peer.bindings.find(row => row.id === path.split("/").at(-1));
      if (!row) throw new Error(`Unknown binding ${path}`);
      if (method === "PATCH" && body.revision !== row.revision) { await route.fulfill({ status: 409, headers, json: {} }); return; }
      if (!peer.history.has(row.id)) record(row, row.workflow_paths == null ? "imported" : "created");
      const enabled = method === "DELETE" ? false : body.enabled ?? row.enabled;
      const action = enabled !== row.enabled ? enabled ? "enabled" : "disabled" : "updated";
      Object.assign(row, body?.configuration, { enabled, revision: row.revision + 1 }); record(row, action);
      if (method === "DELETE") { await route.fulfill({ status: 204, headers }); return; }
      value = row;
    } else if (path === "companies") value = peer.companies;
    else if (path === "computers") value = peer.computers.filter(row => url.searchParams.get("company_id") ? row.company_id === url.searchParams.get("company_id") : !row.company_id);
    else throw new Error(`Unexpected Actions HTTP request ${method} ${path}`);
    await route.fulfill({ headers, json: value });
  }
  await page.route(`${API}/github/**`, handle);
  await page.route(`${API}/companies?*`, handle);
  await page.route(`${API}/computers?*`, handle);
  return peer;
}
