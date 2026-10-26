// Isolated sessions: no access to the user's browser profile or login cookies.
import { chromium } from "playwright";

const proxy = process.argv[2];
const origin = process.argv[3];
const scenarios = [
  { name: "ordinary_dns", rules: "" },
  { name: "force_proxy", rules: `MAP platform.mutating.tech ${proxy}, MAP api.mutating.tech ${proxy}` },
  { name: "api_origin_dashboard_proxy", rules: `MAP platform.mutating.tech ${proxy}, MAP api.mutating.tech ${origin}` },
];

const results = await Promise.all(scenarios.map(async scenario => {
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-proxy-server", ...(scenario.rules ? [`--host-resolver-rules=${scenario.rules}`] : [])],
  });
  const page = await browser.newPage();
  const failures = [];
  const responses = [];
  const pending = [];
  page.on("requestfailed", request => failures.push({
    url: request.url().split("?")[0],
    method: request.method(),
    failure: request.failure()?.errorText,
  }));
  page.on("response", response => {
    if (!response.url().startsWith("https://api.mutating.tech/")) return;
    pending.push((async () => {
      const headers = response.headers();
      responses.push({
        url: response.url().split("?")[0],
        status: response.status(),
        server: await response.serverAddr(),
        allow_origin: headers["access-control-allow-origin"],
        allow_credentials: headers["access-control-allow-credentials"],
      });
    })());
  });
  const result = { scenario: scenario.name };
  try {
    const navigation = await page.goto("https://platform.mutating.tech/", {
      waitUntil: "domcontentloaded", timeout: 15000,
    });
    result.dashboard = { status: navigation.status(), server: await navigation.serverAddr() };
    await page.waitForFunction(() => typeof window.__TMC_API_URL__ === "string", { timeout: 8000 });
    result.api_base = await page.evaluate(() => window.__TMC_API_URL__);
    result.fetches = await page.evaluate(async () => Promise.all([
      "agent-capabilities", "auth/session", "github/status",
    ].map(async path => {
      const start = performance.now();
      try {
        const response = await fetch(`${window.__TMC_API_URL__}/${path}`, {
          credentials: "include",
          headers: { "Content-Type": "application/json", "X-Requested-With": "ThrongDashboard" },
          signal: AbortSignal.timeout(10000),
        });
        return { path, status: response.status, type: response.type, milliseconds: Math.round(performance.now() - start) };
      } catch (error) {
        return { path, error: `${error.name}: ${error.message}`, milliseconds: Math.round(performance.now() - start) };
      }
    })));
  } catch (error) {
    result.error = `${error.name}: ${error.message.split("\n")[0]}`;
  }
  await Promise.allSettled(pending);
  result.api_responses = responses;
  result.request_failures = failures;
  await browser.close();
  return result;
}));
console.log(JSON.stringify(results));
