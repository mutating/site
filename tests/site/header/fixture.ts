import { expect, type Page, type TestInfo } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

export const epoch = new Date("2026-10-16T09:00:00Z");
const referenceDirectory = resolve(import.meta.dirname, "reference");
const manifest = JSON.parse(await readFile(resolve(referenceDirectory, "manifest.json"), "utf8"));
type Shot = { name: string; png: Buffer };

export async function prepare(page: Page, reference: boolean, language: string, theme: string, width: number, reducedMotion = false) {
  await page.setViewportSize({ width, height: 1000 });
  await page.emulateMedia({ colorScheme: theme as "light" | "dark", reducedMotion: reducedMotion ? "reduce" : "no-preference" });
  // Leave a full day for the protocol round trip before pausing at the fixed
  // start. A one-second lead can already be in the past on a busy CI runner.
  await page.clock.install({ time: new Date(epoch.getTime() - 86_400_000) });
  await page.clock.pauseAt(epoch);
  await page.addInitScript(({ language, theme }) => {
    localStorage.setItem("tmc-language", language);
    localStorage.setItem("tmc-theme", theme);
  }, { language, theme });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== "http://127.0.0.1:18121") return route.abort();
    if (/\/(?:muta-tentacles|tokamak-reactor)\.html/.test(url.pathname)) return route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Header test</title>" });
    const asset = "assets" + url.pathname;
    if (reference && manifest.files[asset]) return route.fulfill({ path: resolve(referenceDirectory, asset) });
    return route.continue();
  });
  await page.goto(reference ? `/__header_reference/${language}.html` : language === "ru" ? "/ru/" : "/");
  await expect(page.locator("html")).toHaveAttribute("data-theme-ready", "true");
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(async () => {
    const surface = document.querySelector(".tmc-eye__surface, .animated-eye-logo__surface")!;
    const url = getComputedStyle(surface).maskImage.match(/^url\(["']?(.*?)["']?\)$/)?.[1];
    if (url) {
      const image = new Image();
      image.src = url;
      await image.decode();
    }
  });
  await page.clock.runFor(1024);
  // The selector bridge only describes DOM names. Expected markup, CSS and JS
  // always come from the frozen pre-migration site, never from the library.
  await page.evaluate(() => {
    const names: Record<string, string> = { "animated-eye-logo": "tmc-eye", "site-header": "tmc-header", "main-nav": "tmc-nav" };
    (window as any).headerClass = (name: string) => document.querySelector(".tmc-header")
      ? "." + (names[name] ?? "tmc-" + name)
      : "." + name;
  });
}

export async function selector(page: Page, name: string) {
  return page.evaluate(name => (window as any).headerClass(name), name);
}

export async function capture(page: Page, name: string, height = 160, animationTime = 1000): Promise<Shot> {
  await page.evaluate(time => {
    document.body.getBoundingClientRect();
    for (const animation of document.getAnimations()) { animation.pause(); animation.currentTime = time; }
  }, animationTime);
  return { name, png: await page.screenshot({ clip: { x: 0, y: 0, width: page.viewportSize()!.width, height }, animations: "allow", caret: "hide" }) };
}

export async function compare(expected: Shot[], actual: Shot[], info: TestInfo) {
  expect(actual.map(shot => shot.name)).toEqual(expected.map(shot => shot.name));
  const failures: string[] = [];
  for (let index = 0; index < expected.length; index++) {
    if (actual[index].png.equals(expected[index].png)) continue;
    failures.push(expected[index].name);
    await info.attach(expected[index].name + "-before", { body: expected[index].png, contentType: "image/png" });
    await info.attach(expected[index].name + "-after", { body: actual[index].png, contentType: "image/png" });
  }
  expect(failures, "All header pixels must match the frozen original; no masks or tolerance").toEqual([]);
}

