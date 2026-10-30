// Isolated Chromium profiles with a loopback DoH resolver. The resolver returns
// the two public A records in a fixed order; system DNS and VPN are untouched.
import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createServer } from "node:https";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

process.umask(0o077);
const [origin, proxy] = process.argv.slice(2);
const hostname = "api.mutating.tech";
const directory = await mkdtemp(join(tmpdir(), "mutating-tls-race-"));
const keyPath = join(directory, "resolver.key");
const certPath = join(directory, "resolver.crt");
execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
  "-keyout", keyPath, "-out", certPath, "-days", "1", "-subj", "/CN=localhost",
  "-addext", "subjectAltName=IP:127.0.0.1"], { stdio: "ignore" });
const publicKey = execFileSync("openssl", ["x509", "-in", certPath, "-pubkey", "-noout"]);
const publicDer = execFileSync("openssl", ["pkey", "-pubin", "-outform", "DER"], { input: publicKey });
const resolverSpki = createHash("sha256").update(publicDer).digest("base64");
const questions = [];
const server = createServer({ key: await readFile(keyPath), cert: await readFile(certPath) }, async (request, response) => {
  try {
    let query;
    if (request.method === "GET") {
      query = Buffer.from(new URL(request.url, "https://localhost").searchParams.get("dns"), "base64url");
    } else {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      query = Buffer.concat(chunks);
    }
    let offset = 12;
    const labels = [];
    while (query[offset]) {
      const length = query[offset++];
      labels.push(query.subarray(offset, offset + length).toString("ascii"));
      offset += length;
    }
    offset++;
    const type = query.readUInt16BE(offset);
    const name = labels.join(".").toLowerCase();
    const profile = request.url.split("?")[0].split("/").at(-1);
    const addresses = profile.endsWith("_proxy_first") ? [proxy, origin] : [origin, proxy];
    const answers = name === hostname && type === 1 ? addresses : [];
    questions.push({ name, type, profile, ...(answers.length ? { answers } : {}) });
    const header = Buffer.alloc(12);
    query.copy(header, 0, 0, 2);
    header.writeUInt16BE(0x8180, 2);
    header.writeUInt16BE(1, 4);
    header.writeUInt16BE(answers.length, 6);
    const records = answers.map(address => {
      const record = Buffer.alloc(16);
      record.writeUInt16BE(0xc00c, 0);
      record.writeUInt16BE(1, 2);
      record.writeUInt16BE(1, 4);
      record.writeUInt32BE(30, 6);
      record.writeUInt16BE(4, 10);
      Buffer.from(address.split(".").map(Number)).copy(record, 12);
      return record;
    });
    response.writeHead(200, { "Content-Type": "application/dns-message", "Cache-Control": "no-store" });
    response.end(Buffer.concat([header, query.subarray(12, offset + 4), ...records]));
  } catch {
    response.writeHead(400);
    response.end();
  }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const resolverURL = `https://127.0.0.1:${server.address().port}/dns-query`;

async function probe(name, enabled, channel) {
  const profilePath = join(directory, name);
  await mkdir(profilePath);
  await writeFile(join(profilePath, "Local State"), JSON.stringify({
    dns_over_https: { mode: "secure", templates: `${resolverURL}/${name}{?dns}` },
    built_in_dns_client_enabled: true,
  }));
  const logPath = join(directory, `${name}.json`);
  const result = { name, hev3_explicit: enabled };
  let context;
  try {
    context = await chromium.launchPersistentContext(profilePath, {
      headless: true, ...(channel ? { channel } : {}),
      args: ["--no-proxy-server", `--ignore-certificate-errors-spki-list=${resolverSpki}`,
        `--log-net-log=${logPath}`, `--enable-features=AsyncDns,UseDnsHttpsSvcb${enabled ? ",HappyEyeballsV3" : ""}`],
    });
    result.version = context.browser().version();
    await context.route("https://platform.mutating.tech/**", route => route.fulfill({
      contentType: "text/html", body: "<!doctype html><title>TLS fallback diagnostic</title>",
    }));
    const page = await context.newPage();
    await page.goto("https://platform.mutating.tech/");
    result.fetch = await page.evaluate(async () => {
      const start = performance.now();
      try {
        const response = await fetch("https://api.mutating.tech/v1/agent-capabilities", {
          signal: AbortSignal.timeout(15000),
        });
        const body = await response.json();
        return { status: response.status, protocol_version: body.protocol_version, milliseconds: Math.round(performance.now() - start) };
      } catch (error) {
        return { error: `${error.name}: ${error.message}`, milliseconds: Math.round(performance.now() - start) };
      }
    });
  } catch (error) {
    result.error = error.message.split("\n")[0];
  } finally {
    if (context) await context.close();
  }
  try {
    const log = JSON.parse(await readFile(logPath, "utf8"));
    const types = Object.fromEntries(Object.entries(log.constants.logEventTypes).map(([key, value]) => [value, key]));
    result.hev3_pool_events = log.events.filter(event => types[event.type].startsWith("HTTP_STREAM_POOL_")).length;
    result.api_dns_questions = questions.filter(question => question.profile === name && question.name === hostname);
    result.events = log.events.filter(event => {
      const serialized = JSON.stringify(event.params || {});
      return /HOST_RESOLVER|DNS_TRANSACTION|TCP_CONNECT|TCP_BASED_ATTEMPT|SSL_CONNECT/.test(types[event.type])
        && [origin, proxy].some(address => serialized.includes(address));
    }).map(event => ({ type: types[event.type], source: event.source.id, phase: event.phase, time: event.time, params: event.params })).slice(0, 40);
  } catch (error) {
    result.netlog_error = error.message;
  }
  return result;
}

try {
  const results = [];
  for (const suffix of ["", "_proxy_first"]) {
    results.push(...await Promise.all([
      probe(`chrome_default${suffix}`, false, "chrome"),
      probe(`chrome_hev3${suffix}`, true, "chrome"),
    ]));
  }
  console.log(JSON.stringify({ resolver: "loopback-only; fixed A order", addresses: [origin, proxy], questions, results }));
} finally {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  await rm(directory, { recursive: true, force: true });
}
