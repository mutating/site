import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile, readdir } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { prepareHeaderReference } from "./prepare-reference.mjs";

const repository = fileURLToPath(new URL("../../", import.meta.url));
const ui = resolve(repository, "packages/ui");

test("the core and React entries can be imported and server rendered without a browser", async () => {
  const core = await import("@mutating/ui");
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const { Header, Brand, LanguageSwitcher, LanguageToggle, ThemeProvider, ThemeToggle, Button } = await import("@mutating/ui/react");
  assert.equal(typeof core.mountHeader, "function");
  const html = renderToStaticMarkup(createElement(ThemeProvider, null,
    createElement(Header, { brand: { href: "/" } }),
    createElement(Button, null, "Action"),
  ));
  assert.match(html, /tmc-eye__surface/);
  assert.match(html, /aria-label="The Mutating Company"/);
  assert.match(html, /type="button"/);
  const lightToggle = renderToStaticMarkup(createElement(ThemeProvider, { initialTheme: "light" }, createElement(ThemeToggle)));
  assert.match(lightToggle, /aria-pressed="false"/);
  const emptyHeader = renderToStaticMarkup(createElement(Header, { items: [], showThemeToggle: false }));
  assert.doesNotMatch(emptyHeader, /<nav|tmc-mobile-menu|tmc-theme-toggle/);
  const applicationHeader = renderToStaticMarkup(createElement(Header, {
    showThemeToggle: false,
    actions: createElement(LanguageToggle, { current: "ru", "aria-label": "Choose language" }),
  }));
  assert.match(applicationHeader, /<button[^>]*aria-label="Choose language"[^>]*type="button"[^>]*class="tmc-language-toggle">RU<\/button>/);
  assert.doesNotMatch(applicationHeader, /<nav|tmc-mobile-menu/);
  const reloadBrand = renderToStaticMarkup(createElement(Brand, { href: "/ru/", animation: { activation: "reload-home" } }));
  assert.match(reloadBrand, /^<div[^>]*role="link"[^>]*tabindex="0"[^>]*data-home-url="\/ru\/"/);
  assert.doesNotMatch(reloadBrand, /href=|draggable=/);
  const linkBrand = renderToStaticMarkup(createElement(Brand, { href: "/dashboard/" }));
  assert.match(linkBrand, /^<a[^>]*href="\/dashboard\/"/);
  const focusBrand = renderToStaticMarkup(createElement(Brand, { href: "/dashboard/", animation: { expandOnFocus: true } }));
  assert.match(focusBrand, /tabindex="0"/);
  const language = renderToStaticMarkup(createElement(LanguageSwitcher, {
    current: "en", options: [{ code: "ru", href: "/ru/" }],
    supportedCodes: ["en", "ru", "zh-hant-tw"], label: "Choose language", groupLabel: "Languages",
  }));
  assert.match(language, /--tmc-language-switcher-desktop-width:94px/);
  assert.doesNotMatch(language, /data-language-option="zh-hant-tw"/);
  const relations = renderToStaticMarkup(createElement(Header, {
    showThemeToggle: false,
    items: [
      { id: "default", href: "/default/", label: "Default", target: "_blank" },
      { id: "empty", href: "/empty/", label: "Empty", target: "_blank", rel: "" },
      { id: "explicit", href: "/explicit/", label: "Explicit", target: "_blank", rel: "noopener" },
    ],
  }));
  assert.match(relations, /href="\/default\/"[^>]*rel="noopener noreferrer"/);
  assert.match(relations, /href="\/empty\/"[^>]*rel=""/);
  assert.match(relations, /href="\/explicit\/"[^>]*rel="noopener"/);
});

test("all CSS assets resolve inside the self-contained distribution", async () => {
  for (const file of await readdir(resolve(ui, "dist/styles"))) {
    const path = resolve(ui, "dist/styles", file);
    const css = await readFile(path, "utf8");
    for (const match of css.matchAll(/(?:url\(|@import\s+)"([^"]+)"/g)) {
      assert.ok(!match[1].startsWith("/"), `root-relative asset in ${file}: ${match[1]}`);
      const asset = await readFile(resolve(path, "..", match[1]));
      assert.ok(asset.length > 0);
    }
  }
});

test("Hugo renders the adapters without depending on the company site's templates", async () => {
  execFileSync("hugo", ["--source", resolve(repository, "tests/ui/hugo"), "--destination", resolve(ui, ".runtime/hugo"), "--noBuildLock", "--panicOnWarning"], { encoding: "utf8" });
  const html = await readFile(resolve(ui, ".runtime/hugo/index.html"), "utf8");
  assert.match(html, /Переключить тему/);
  assert.match(html, /tmc-language-switcher--desktop/);
  assert.match(html, /tmc-language-switcher--mobile/);
  assert.match(html, /disabled/);
  assert.doesNotMatch(html, /ZgotmplZ|<no value>|{{/);
});

test("the pixel reference is built from the original home page in both languages", prepareHeaderReference);
