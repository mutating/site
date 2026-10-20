import { execFileSync } from "node:child_process";
import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { createMutatingHomeHeader } from "@mutating/ui";
import { Header, ThemeProvider } from "@mutating/ui/react";

const repository = fileURLToPath(new URL("../../", import.meta.url));
const runtime = resolve(repository, "packages/ui/.runtime");
const frozen = resolve(repository, "tests/site/header/reference");

/** The expected renderer is the frozen site from before its header migration. */
export async function prepareHeaderReference() {
  await mkdir(runtime, { recursive: true });
  execFileSync("hugo", ["--source", resolve(repository, "site"), "--destination", resolve(runtime, "site"), "--noBuildLock", "--panicOnWarning", "--baseURL", "http://127.0.0.1:18120/"], { encoding: "utf8" });
  await cp(resolve(frozen, "assets"), resolve(runtime, "site"), { recursive: true });
  const configs = { en: createMutatingHomeHeader("en"), ru: createMutatingHomeHeader("ru") };
  const fixture = resolve(runtime, "home-hugo");
  await mkdir(resolve(fixture, "layouts"), { recursive: true });
  await writeFile(resolve(fixture, "hugo.toml"), `baseURL = "https://ui.example.test/"\ndisableKinds = ["taxonomy", "term", "RSS", "sitemap"]\n[[module.mounts]]\nsource = ${JSON.stringify(resolve(repository, "packages/ui/hugo"))}\ntarget = "layouts"\n[[module.mounts]]\nsource = "layouts"\ntarget = "layouts"\n[[module.mounts]]\nsource = ${JSON.stringify(resolve(repository, "packages/ui/dist/presets"))}\ntarget = "data/tmc_ui_home"\n`);
  await writeFile(resolve(fixture, "layouts/index.html"), '{{ $result := dict }}{{ range $lang, $config := hugo.Data.tmc_ui_home }}{{ $result = merge $result (dict $lang (partial "tmc-ui/header.html" $config)) }}{{ end }}{{ $result | jsonify | safeHTML }}');
  execFileSync("hugo", ["--source", fixture, "--destination", resolve(fixture, "public"), "--noBuildLock", "--panicOnWarning"], { encoding: "utf8" });
  const hugoHeaders = JSON.parse(await readFile(resolve(fixture, "public/index.html"), "utf8"));

  const source = await readFile(resolve(frozen, "site.js"), "utf8");
  const names = ["initTheme", "initLanguageSwitcher", "initAnimatedWordmark", "initBrandControl", "initAnimatedEye", "initMobileMenus"];
  let referenceScript = source.slice(0, source.indexOf("  function initCurrentYear"));
  for (const name of names) {
    const start = source.indexOf(`  function ${name}()`);
    const end = source.indexOf("\n  function ", start + 1);
    referenceScript += source.slice(start, end);
  }
  referenceScript += `\ndocument.fonts.ready.then(() => { ${names.map((name) => `${name}();`).join(" ")} document.documentElement.dataset.uiReady = "true"; });\n})();`;
  await writeFile(resolve(runtime, "header-reference.js"), referenceScript);

  for (const language of ["en", "ru"]) {
    const original = await readFile(resolve(frozen, `${language}.html`), "utf8");
    await mkdir(resolve(runtime, "site/__header_reference"), { recursive: true });
    await writeFile(resolve(runtime, "site/__header_reference/site.css"), await readFile(resolve(frozen, "site.css")));
    const header = original.match(/<header class="site-header-shell">[\s\S]*?<\/header>/)?.[0];
    if (!header) throw new Error("The current site's header was not found; update the reference fixture explicitly.");
    for (const adapter of ["reference", "hugo", "react"]) {
      const directory = resolve(runtime, "pixel", adapter, language);
      await mkdir(directory, { recursive: true });
      let html = original.replace(/<script\b[^>]*\bsrc=[^>]*>[\s\S]*?<\/script>/g, "");
      const config = configs[language];
      const markup = adapter === "reference" ? header : adapter === "hugo" ? hugoHeaders[language]
        : renderToString(createElement(ThemeProvider, null, createElement(Header, config)));
      html = html.replace(header, `<div id="header-root" style="display:contents">${markup}</div>`);
      if (adapter !== "reference") html = html.replace("</head>", '<link rel="stylesheet" href="/dist/styles/index.css"></head>');
      html = html.replace("</body>", `<script type="application/json" id="header-config">${JSON.stringify(config).replaceAll("<", "\\u003c")}</script>${adapter === "reference" ? '<script defer src="/.runtime/header-reference.js"></script>' : `<script type="module" src="/demo/parity.tsx"></script>`}</body>`);
      html = html.replace("<html ", `<html data-ui-adapter="${adapter}" `);
      await writeFile(resolve(directory, "index.html"), html);
    }
  }
}
