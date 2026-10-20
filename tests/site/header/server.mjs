import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { createHash } from "node:crypto";

const root = resolve(import.meta.dirname, "../../..");
const reference = resolve(import.meta.dirname, "reference");
const output = resolve(import.meta.dirname, ".runtime/site");
const manifest = JSON.parse(await readFile(resolve(reference, "manifest.json"), "utf8"));
for (const [path, hash] of Object.entries(manifest.files)) {
  const bytes = await readFile(resolve(reference, path));
  if (createHash("sha256").update(bytes).digest("hex") !== hash) throw new Error(`Frozen reference changed: ${path}`);
}
await mkdir(output, { recursive: true });
execFileSync("hugo", ["--source", "site", "--destination", output, "--baseURL", "http://127.0.0.1:18121/", "--noBuildLock", "--panicOnWarning"], { cwd: root, stdio: "inherit" });
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml" };
createServer(async (request, response) => {
  let path = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
  const isReference = path.startsWith("/__header_reference/");
  const directory = isReference ? reference : output;
  path = isReference ? path.slice("/__header_reference/".length) : path.slice(1);
  if (!path || path.endsWith("/")) path += "index.html";
  const file = resolve(directory, path);
  if (!file.startsWith(directory + sep)) { response.writeHead(403).end(); return; }
  try { response.writeHead(200, { "Content-Type": types[extname(file)] ?? "application/octet-stream" }); response.end(await readFile(file)); }
  catch { response.writeHead(404).end(); }
}).listen(18121, "127.0.0.1");
