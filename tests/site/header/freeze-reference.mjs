// Explicit, one-time capture. Normal test runs never rewrite this reference.
import { execFileSync } from "node:child_process";
import { mkdir, readFile, writeFile, access } from "node:fs/promises";
import { resolve } from "node:path";
import { createHash } from "node:crypto";

const root = resolve(import.meta.dirname, "../../..");
const reference = resolve(import.meta.dirname, "reference");
try { await access(reference); throw new Error("Reference already exists; review any replacement explicitly."); }
catch (error) { if (error.code !== "ENOENT") throw error; }
const output = resolve(import.meta.dirname, ".runtime/capture");
execFileSync("hugo", ["--source", "site", "--destination", output, "--baseURL", "http://127.0.0.1:18121/", "--noBuildLock", "--panicOnWarning"], { cwd: root, stdio: "inherit" });
await mkdir(reference, { recursive: true });
const hashes = {};
async function save(path, data) {
  await mkdir(resolve(reference, path, ".."), { recursive: true });
  await writeFile(resolve(reference, path), data);
  hashes[path] = createHash("sha256").update(data).digest("hex");
}
for (const language of ["en", "ru"]) {
  let html = await readFile(resolve(output, language === "en" ? "index.html" : "ru/index.html"), "utf8");
  const css = html.match(/<link rel="stylesheet" href="([^"]+)"[^>]*>/)[1];
  html = html.replace(/<link rel="stylesheet" href="[^"]+"[^>]*>/, '<link rel="stylesheet" href="/__header_reference/site.css">');
  html = html.replace(/<script\b[^>]*\bsrc=[^>]*>[\s\S]*?<\/script>/g, "");
  html = html.replace("</body>", '<script defer src="/__header_reference/site.js"></script></body>');
  await save(`${language}.html`, html);
  const stylesheet = await readFile(resolve(output, css.slice(1)), "utf8");
  await save("site.css", stylesheet);
  const assets = new Set([...stylesheet.matchAll(/url\(["']?(\/[^)"']+)["']?\)/g)].map(match => match[1]));
  for (const asset of assets) await save(`assets${asset}`, await readFile(resolve(output, asset.slice(1))));
}
await save("site.js", await readFile(resolve(root, "site/themes/mutating/assets/js/site.js")));
await writeFile(resolve(reference, "manifest.json"), JSON.stringify({
  description: "Original static site before the header migrated to @mutating/ui. SHA-256 guards the frozen renderer and its assets.",
  commit: execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim(),
  files: hashes,
}, null, 2) + "\n");
