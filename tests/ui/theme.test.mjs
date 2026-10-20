import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import { createThemeController, initializeTheme } from "@mutating/ui";

function environment({ host = "mutating.tech", cookie = "", stored = null, scheme = "dark", denyStorage = false, denyCookie = false } = {}) {
  let value = stored;
  let cookies = cookie;
  const writes = [];
  const window = new EventTarget();
  window.location = { hostname: host, protocol: "https:" };
  window.CustomEvent = CustomEvent;
  window.Event = Event;
  window.localStorage = {
    getItem() { if (denyStorage) throw Error("Denied"); return value; },
    setItem(key, next) { if (denyStorage) throw Error("Denied"); value = next; },
    removeItem() { if (denyStorage) throw Error("Denied"); value = null; },
  };
  const media = new Map();
  window.matchMedia = query => {
    if (!media.has(query)) {
      const result = new EventTarget();
      Object.defineProperty(result, "matches", { get: () => query === `(prefers-color-scheme: ${scheme})` });
      media.set(query, result);
    }
    return media.get(query);
  };
  const document = { defaultView: window, currentScript: { dataset: { tmcThemeCookieDomain: "mutating.tech" } } };
  Object.defineProperty(document, "cookie", {
    get() { if (denyCookie) throw Error("Denied"); return cookies; },
    set(next) { if (denyCookie) throw Error("Denied"); writes.push(next); cookies = next.split(";")[0]; },
  });
  const root = new EventTarget();
  root.ownerDocument = document;
  root.dataset = {};
  document.documentElement = root;
  window.document = document;
  return {
    root, window, document, writes,
    stored: () => value,
    system(next) { scheme = next; for (const result of media.values()) result.dispatchEvent(new Event("change")); },
    setCookie(next) { cookies = next; },
    setStored(next) { value = next; },
  };
}

for (const theme of ["light", "dark"]) {
  test(`shared ${theme} cookie overrides conflicting per-origin storage`, () => {
    const env = environment({ host: "platform.mutating.tech", cookie: `unrelated=1; tmc-theme=${theme}`, stored: theme === "light" ? "dark" : "light" });
    assert.equal(initializeTheme({ cookieDomain: "mutating.tech" }, env.root), theme);
    assert.equal(env.writes.length, 0);
  });
  test(`legacy static ${theme} preference migrates with domain, expiry and secure attributes`, () => {
    const env = environment({ stored: theme });
    assert.equal(initializeTheme({ cookieDomain: "mutating.tech" }, env.root), theme);
    assert.deepEqual(env.writes, [`tmc-theme=${theme}; Domain=mutating.tech; Path=/; Max-Age=31536000; SameSite=Lax; Secure`]);
  });
}

for (const host of ["localhost", "127.0.0.1", "evilmutating.tech", "mutating.tech.example.org"]) {
  test(`shared cookie is never used or written outside the configured domain: ${host}`, () => {
    const env = environment({ host, stored: "light", cookie: "tmc-theme=dark" });
    assert.equal(initializeTheme({ cookieDomain: "mutating.tech" }, env.root), "light");
    assert.equal(env.writes.length, 0);
  });
}

for (const stored of [null, "", "sepia"]) test(`invalid legacy choice ${JSON.stringify(stored)} follows the system without setting a cookie`, () => {
  const env = environment({ stored, scheme: "light", cookie: "tmc-theme=garbage" });
  assert.equal(initializeTheme({ cookieDomain: "mutating.tech" }, env.root), "light");
  assert.equal(env.writes.length, 0);
});

test("useSystemTheme overrides stale sibling storage and later follows OS changes", () => {
  const env = environment({ stored: "dark", scheme: "light" });
  const options = { root: env.root, cookieDomain: "mutating.tech", preferenceSource: "storage" };
  const controller = createThemeController(options);
  controller.useSystemTheme();
  assert.equal(controller.getTheme(), "light");
  assert.equal(env.stored(), null);
  assert.match(env.writes.at(-1), /^tmc-theme=system;/);
  env.setStored("dark");
  assert.equal(initializeTheme(options, env.root), "light");
  env.system("dark");
  assert.equal(controller.getTheme(), "dark");
  env.system("light");
  assert.equal(controller.getTheme(), "light");
  controller.destroy();
});

test("shared manual choices retain the current document until reload, matching the original site", () => {
  const env = environment({ stored: "light", scheme: "light" });
  const controller = createThemeController({ root: env.root, cookieDomain: "mutating.tech", syncAcrossTabs: false, preferenceSource: "storage" });
  env.setCookie("tmc-theme=dark");
  assert.equal(controller.getTheme(), "light");
  env.system("dark");
  assert.equal(controller.getTheme(), "light");
  assert.equal(initializeTheme({ cookieDomain: "mutating.tech" }, env.root), "dark");
  env.setCookie("tmc-theme=light");
  env.system("light");
  assert.equal(controller.getTheme(), "light");
  controller.destroy();
});

for (const denyStorage of [false, true]) for (const denyCookie of [false, true]) {
  test(`theme controller remains usable with storage denied=${denyStorage}, cookies denied=${denyCookie}`, () => {
    const env = environment({ denyStorage, denyCookie });
    const controller = createThemeController({ root: env.root, cookieDomain: "mutating.tech" });
    controller.toggle();
    assert.equal(controller.getTheme(), "light");
    env.system("light");
    env.system("dark");
    assert.equal(controller.getTheme(), "light");
    controller.destroy();
    controller.toggle();
    assert.equal(controller.getTheme(), "light");
  });
}

test("the blocking bootstrap and the module initializer use the same precedence and fallback", async () => {
  const script = await readFile(new URL("../../packages/ui/dist/theme-bootstrap.js", import.meta.url), "utf8");
  for (const options of [
    { stored: "dark", scheme: "light" },
    { cookie: "tmc-theme=light", stored: "dark" },
    { scheme: "none" },
    { denyStorage: true, denyCookie: true, scheme: "light" },
  ]) {
    const env = environment(options);
    runInNewContext(script, { document: env.document });
    const expected = environment(options);
    assert.equal(env.root.dataset.theme, initializeTheme({ cookieDomain: "mutating.tech" }, expected.root));
  }
});
