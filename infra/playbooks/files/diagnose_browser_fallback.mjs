// Read-only fetches in fresh profiles. No changes to DNS, VPN or user browsers.
import { chromium, firefox, webkit } from "playwright";
import { lookup } from "node:dns/promises";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const hostname = "api.mutating.tech";
const addresses = await lookup(hostname, { all: true });
const profiles = process.argv.includes("--chromium-only")
  ? [1, 2, 3].flatMap(round => [
    { name: `chromium_default_${round}`, engine: chromium },
    { name: `chromium_hev3_enabled_${round}`, engine: chromium, enabled: true },
  ])
  : [
  { name: "chrome_default", engine: chromium, channel: "chrome" },
  { name: "chrome_hev3_enabled", engine: chromium, channel: "chrome", enabled: true },
  { name: "chromium_default", engine: chromium },
  { name: "chromium_hev3_enabled", engine: chromium, enabled: true },
  { name: "firefox_default", engine: firefox },
  { name: "firefox_hev3_enabled", engine: firefox, enabled: true },
  { name: "webkit_default_not_safari", engine: webkit },
];

async function probe(profile) {
  const directory = await mkdtemp(join(tmpdir(), "mutating-browser-fallback-"));
  const logPath = join(directory, "netlog.json");
  const result = { profile: profile.name };
  let browser;
  try {
    const options = { headless: true };
    if (profile.channel) options.channel = profile.channel;
    if (profile.engine === chromium) {
      options.args = ["--no-proxy-server", `--log-net-log=${logPath}`];
      if (profile.enabled) options.args.push("--enable-features=HappyEyeballsV3");
    }
    if (profile.engine === firefox) {
      options.firefoxUserPrefs = { "network.proxy.type": 0 };
      if (profile.enabled) options.firefoxUserPrefs["network.http.happy_eyeballs_enabled"] = true;
    }
    browser = await profile.engine.launch(options);
    result.version = browser.version();
    const page = await browser.newPage();
    // Use a local document with the dashboard's real origin. Only API requests
    // go to the network; the dashboard's own DNS choice cannot mask this test.
    await page.route("https://platform.mutating.tech/**", route => route.fulfill({
      contentType: "text/html", body: "<!doctype html><title>API transport diagnostic</title>",
    }));
    await page.goto("https://platform.mutating.tech/");
    const responses = [];
    const pending = [];
    page.on("response", response => {
      if (!response.url().startsWith(`https://${hostname}/`)) return;
      pending.push((async () => responses.push({
        status: response.status(), server: await response.serverAddr(),
      }))());
    });
    result.fetch = await page.evaluate(async () => {
      const start = performance.now();
      try {
        const response = await fetch("https://api.mutating.tech/v1/agent-capabilities", {
          credentials: "include",
          headers: { "Content-Type": "application/json", "X-Requested-With": "ThrongDashboard" },
          signal: AbortSignal.timeout(15000),
        });
        const body = await response.json();
        return { status: response.status, protocol_version: body.protocol_version, milliseconds: Math.round(performance.now() - start) };
      } catch (error) {
        return { error: `${error.name}: ${error.message}`, milliseconds: Math.round(performance.now() - start) };
      }
    });
    await Promise.allSettled(pending);
    result.responses = responses;
  } catch (error) {
    result.error = error.message.split("\n")[0];
  } finally {
    if (browser) await browser.close();
  }
  if (profile.engine === chromium) {
    try {
      const log = JSON.parse(await readFile(logPath, "utf8"));
      const types = Object.fromEntries(Object.entries(log.constants.logEventTypes).map(([key, value]) => [value, key]));
      result.network_events = log.events.filter(event => {
        const serialized = JSON.stringify(event.params || {});
        return /HOST_RESOLVER|TCP_CONNECT|TCP_BASED_ATTEMPT/.test(types[event.type])
          && addresses.some(address => serialized.includes(address.address));
      }).map(event => ({ type: types[event.type], source: event.source.id, phase: event.phase, time: event.time, params: event.params })).slice(0, 30);
    } catch (error) {
      result.netlog_error = error.message;
    }
  }
  await rm(directory, { recursive: true, force: true });
  return result;
}

const results = [];
// Bound browser concurrency and keep each fetch deadline independent.
for (let offset = 0; offset < profiles.length; offset += 3) {
  results.push(...await Promise.all(profiles.slice(offset, offset + 3).map(probe)));
}
console.log(JSON.stringify({ system_dns: addresses, results }));
