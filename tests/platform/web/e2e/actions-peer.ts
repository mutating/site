import type { Page, Route } from "@playwright/test";
import { API } from "./fixtures";

export const repository = { id: 789, full_name: "mutating/ci-sandbox", private: true, default_branch: "main", can_administer: true };
export const installation = { id: 456, login: "mutating", repository_selection: "selected", suspended: false };
export const binding = {
  id: "11111111-1111-4111-8111-111111111111", repository: repository.full_name,
  installation_id: 456, repository_id: 789, workflow_path: ".github/workflows/ci.yml", job_key: "test", ref: "refs/heads/main",
  company_id: null, computer_ids: [], cpu_millis: 2000, memory_bytes: 4 * 1024 ** 3,
  disk_bytes: 10 * 1024 ** 3, max_parallel: 1, preparation_seconds: 90, timeout_seconds: 3600,
  fallback: "ubuntu-24.04-arm", enabled: true,
};

/** Stateful HTTP peer. Only the browser's external responses are replaced. */
export async function actionsPeer(page: Page) {
  const peer = {
    connected: true,
    bindings: [] as Record<string, unknown>[],
    installations: [installation] as Record<string, unknown>[],
    repositories: [repository] as Record<string, unknown>[],
    companies: [] as Record<string, unknown>[],
    computers: [] as Record<string, unknown>[],
    runs: [] as Record<string, unknown>[],
    calls: [] as { method: string; path: string; query: URLSearchParams; body: Record<string, unknown> | null }[],
    failures: new Map<string, number[]>(),
    custom: null as ((route: Route, url: URL) => Promise<boolean>) | null,
  };
  const headers = { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" };
  async function handle(route: Route) {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/v1/", "");
    const method = route.request().method();
    if (method === "OPTIONS") { await route.fulfill({ status: 204, headers: { ...headers, "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS", "Access-Control-Allow-Headers": "content-type,x-requested-with" } }); return; }
    peer.calls.push({ method, path, query: url.searchParams, body: route.request().postData() ? route.request().postDataJSON() : null });
    const failure = peer.failures.get(`${method} ${path}`)?.shift();
    if (failure) { await route.fulfill({ status: failure, headers, json: { detail: "Temporary peer failure" } }); return; }
    if (peer.custom && await peer.custom(route, url)) return;
    let json: unknown;
    if (path === "github/status") json = { enabled: true, account: peer.connected ? { id: 123, login: "github-user" } : null };
    else if (path === "github/authorize" || path === "github/installations/authorize") json = { url: "https://github.com/login/oauth/authorize?state=test-state" };
    else if (path === "github/connection") { peer.connected = false; await route.fulfill({ status: 204, headers }); return; }
    else if (path === "github/installations") json = { total_count: peer.installations.length, installations: peer.installations };
    else if (/github\/installations\/\d+\/repositories/.test(path)) json = { total_count: peer.repositories.length, repositories: peer.repositories };
    else if (path === "github/routes" && method === "POST") {
      json = { ...binding, ...route.request().postDataJSON() };
      peer.bindings.push(json as Record<string, unknown>);
      await route.fulfill({ status: 201, headers, json }); return;
    } else if (path === "github/routes") json = peer.bindings;
    else if (path.endsWith("/workflow")) json = { yaml: `# ${path}\njobs:\n  test:\n    needs: powercell_route\n` };
    else if (path.endsWith("/runs")) json = peer.runs;
    else if (method === "DELETE" && path.startsWith("github/routes/")) {
      const row = peer.bindings.find((row) => row.id === path.split("/").at(-1));
      if (!row) throw new Error(`Unknown binding ${path}`);
      row.enabled = false;
      await route.fulfill({ status: 204, headers }); return;
    } else if (path === "companies") json = peer.companies;
    else if (path === "computers") json = peer.computers;
    else throw new Error(`Unexpected Actions HTTP request ${method} ${path}`);
    await route.fulfill({ headers, json });
  }
  await page.route(`${API}/github/**`, handle);
  await page.route(`${API}/companies?*`, handle);
  await page.route(`${API}/computers?*`, handle);
  return peer;
}
