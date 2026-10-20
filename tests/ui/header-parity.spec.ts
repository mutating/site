import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const runtime = resolve(import.meta.dirname, "../../packages/ui/.runtime");
const time = new Date("2026-10-16T09:00:00Z");

async function prepare(page: Page, adapter: string, language: string, width: number, theme: "light" | "dark", reducedMotion = false) {
  await page.setViewportSize({ width, height: 1000 });
  await page.emulateMedia({ colorScheme: theme, reducedMotion: reducedMotion ? "reduce" : "no-preference" });
  await page.clock.install({ time: new Date(time.getTime() - 86_400_000) });
  await page.clock.pauseAt(time);
  await page.addInitScript(({ language, theme }) => {
    localStorage.setItem("tmc-language", language);
    localStorage.setItem("tmc-theme", theme);
  }, { language, theme });
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== "http://127.0.0.1:18120") return route.abort();
    if (url.pathname.startsWith("/.runtime/") || url.pathname.startsWith("/dist/") || url.pathname.startsWith("/src/") || url.pathname.startsWith("/demo/") || url.pathname.startsWith("/@") || url.pathname.startsWith("/node_modules/")) return route.continue();
    if (url.pathname.endsWith(".html")) return route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Static header test</title>" });
    try { return await route.fulfill({ path: resolve(runtime, "site", url.pathname.slice(1)) }); }
    catch { return route.abort(); }
  });
  await page.goto(`/.runtime/pixel/${adapter}/${language}/index.html`);
  await expect(page.locator("html")).toHaveAttribute("data-ui-ready", "true");
  await page.clock.runFor(1024);
  await freeze(page);
}

async function freeze(page: Page, animationTime = 1000) {
  await page.evaluate((animationTime) => {
    // All CSS timelines use the same timestamp. Nothing is masked or hidden.
    document.body.getBoundingClientRect();
    for (const animation of document.getAnimations()) { animation.pause(); animation.currentTime = animationTime; }
  }, animationTime);
}

async function compare(expected: Buffer, candidate: Page, state: string, info: TestInfo, height = 160) {
  const clip = { x: 0, y: 0, width: candidate.viewportSize()!.width, height };
  const adapter = await candidate.locator("html").getAttribute("data-ui-adapter");
  const name = `${adapter}-${state}.png`;
  const path = info.snapshotPath(name);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, expected);
  const actual = await candidate.screenshot({ clip, animations: "allow" });
  if (!actual.equals(expected)) {
    await info.attach(name + "-expected", { body: expected, contentType: "image/png" });
    await info.attach(name + "-actual", { body: actual, contentType: "image/png" });
    await writeFile(info.outputPath(`${adapter}-${state}-raw.png`), actual);
    await expect(candidate).toHaveScreenshot(name, { clip, animations: "allow", threshold: 0, maxDiffPixels: 0 });
  }
  expect(actual.equals(expected), `${adapter} ${state}: screenshots must be byte-identical`).toBe(true);
}

async function mountCandidate(page: Page, adapter: string, language: string) {
  const html = await readFile(resolve(runtime, "pixel", adapter, language, "index.html"), "utf8");
  const markup = html.match(/<div id="header-root" style="display:contents">([\s\S]*?<\/header>)/)![1];
  if (adapter === "hugo") await page.addStyleTag({ url: "/dist/styles/index.css" });
  await page.evaluate(async ({ markup, adapter }) => {
    document.documentElement.dataset.uiReady = "false";
    document.documentElement.dataset.uiAdapter = adapter;
    document.querySelector("#header-root")!.innerHTML = markup;
    const path = "/demo/parity.tsx?instance=" + performance.now();
    await import(path);
  }, { markup, adapter });
  await expect(page.locator("html")).toHaveAttribute("data-ui-ready", "true");
  await page.clock.runFor(1024);
  await freeze(page);
}

for (const width of [1440, 1241, 1240, 1121, 1120, 1041, 1040, 921, 920, 861, 860, 581, 580, 481, 480, 381, 380, 360, 280]) {
  for (const language of ["en", "ru"]) for (const theme of ["light", "dark"] as const) {
    test(`${language} ${theme} ${width}px matches the current home header exactly`, async ({ context }, info) => {
      const page = await context.newPage();
      await prepare(page, "reference", language, width, theme);
      const expected = await page.screenshot({ clip: { x: 0, y: 0, width, height: 160 }, animations: "allow" });
      for (const adapter of ["hugo", "react"]) {
        await mountCandidate(page, adapter, language);
        await compare(expected, page, "rest", info);
      }
      await page.close();
    });
  }
}

interface StateImage { name: string; png: Buffer; height: number }
async function captureStates(page: Page): Promise<StateImage[]> {
  const source = await page.locator("html").getAttribute("data-ui-adapter") === "reference";
  const cls = (name: string) => `.${source ? name : "tmc-" + name}`;
  const pictures: StateImage[] = [];
  const take = async (name: string, height = 160, animationTime = 1000) => {
    await freeze(page, animationTime);
    pictures.push({ name, height, png: await page.screenshot({ clip: { x: 0, y: 0, width: page.viewportSize()!.width, height }, animations: "allow" }) });
  };
  await page.mouse.move(0, 901);
  await page.mouse.move(0, 900);
  await page.clock.runFor(1024);
  await page.keyboard.press("Tab");
  await page.locator(cls("brand")).focus();
  await take("brand-focus");
  await page.locator(cls("brand")).blur();

  if (page.viewportSize()!.width >= 921) {
    const bounds = await page.locator(cls("brand__wordmark")).boundingBox();
    await page.mouse.move(bounds!.x + 5, bounds!.y + bounds!.height / 2);
    for (const [advance, time] of [[80, 80], [96, 176], [176, 352]]) {
      await page.clock.runFor(advance);
      await take(`wordmark-${time}`, 160, time);
    }
    await page.mouse.move(0, 900);
    await page.clock.runFor(1024);
    await take("wordmark-collapsed");
  }

  const mobile = page.viewportSize()!.width <= 1120;
  if (mobile) {
    await page.locator(`${cls("mobile-menu")} > summary`).evaluate((node: HTMLElement) => { node.focus(); node.click(); });
    await page.clock.runFor(320);
    await take("mobile-menu-open", 560);
  }
  const language = cls(`language-switcher--${mobile ? "mobile" : "desktop"}`);
  await page.locator(`${language} > summary`).evaluate((node: HTMLElement) => { node.focus(); node.click(); });
  await page.clock.runFor(320);
  await take("language-open", mobile ? 560 : 280);
  await page.evaluate(() => {
    document.querySelectorAll("details[open]").forEach(node => node.removeAttribute("open"));
    // Commit the menu's closing styles before changing the global theme.
    // Otherwise these two independent transitions can start in different
    // rendering turns when replacing the header in the same document.
    const header = document.querySelector("header")!;
    header.getBoundingClientRect();
    header.getAnimations({ subtree: true }).forEach(animation => animation.finish());
  });

  await page.locator(cls("theme-toggle")).evaluate((node: HTMLElement) => { node.focus(); node.click(); });
  for (const [advance, time] of [[80, 80], [96, 176], [240, 416]]) {
    await page.clock.runFor(advance);
    await take(`theme-and-blink-${time}`, 160, time);
  }
  await page.evaluate(() => {
    (document.activeElement as HTMLElement)?.blur();
    window.scrollTo({ top: 500, behavior: "instant" });
  });
  await page.clock.runFor(1024);
  await take("scrolled-glass-background");
  await page.evaluate(() => window.dispatchEvent(new Event("tmc-brand-home-activate")));
  await page.clock.runFor(80);
  await take("home-eye-closing", 160, 80);
  await page.clock.runFor(192);
  await take("home-eye-held-closed");
  return pictures;
}

for (const width of [1440, 1120, 480, 360]) {
  for (const language of ["en", "ru"]) for (const theme of ["light", "dark"] as const) {
    test(`${language} ${theme} ${width}px interaction states and animation frames match exactly`, async ({ context }, info) => {
      test.setTimeout(90_000);
      const page = await context.newPage();
      await prepare(page, "reference", language, width, theme);
      const expected = await captureStates(page);
      for (const adapter of ["hugo", "react"]) {
        await page.evaluate((theme) => {
          window.scrollTo({ top: 0, behavior: "instant" });
          document.documentElement.dataset.theme = theme;
          localStorage.setItem("tmc-theme", theme);
        }, theme);
        await mountCandidate(page, adapter, language);
        const actual = await captureStates(page);
        expect(actual.map(image => image.name)).toEqual(expected.map(image => image.name));
        for (let i = 0; i < actual.length; i++) {
          // Compare the captured frame, without recapturing after later actions.
          const name = `${adapter}-${actual[i].name}.png`;
          if (!actual[i].png.equals(expected[i].png)) {
            await writeFile(info.outputPath(name + "-expected.png"), expected[i].png);
            await writeFile(info.outputPath(name + "-actual.png"), actual[i].png);
            await info.attach(name + "-expected", { body: expected[i].png, contentType: "image/png" });
            await info.attach(name + "-actual", { body: actual[i].png, contentType: "image/png" });
          }
          expect(actual[i].png.equals(expected[i].png), `${adapter}: ${actual[i].name} must be byte-identical`).toBe(true);
        }
      }
      await page.close();
    });
  }
}

for (const width of [1440, 360]) {
  test(`reduced motion ${width}px preserves the original interaction frames`, async ({ context }, info) => {
    test.setTimeout(90_000);
    const page = await context.newPage();
    await prepare(page, "reference", "ru", width, "dark", true);
    const expected = await captureStates(page);
    for (const adapter of ["hugo", "react"]) {
      await page.evaluate(() => {
        window.scrollTo({ top: 0, behavior: "instant" });
        document.documentElement.dataset.theme = "dark";
        localStorage.setItem("tmc-theme", "dark");
      });
      await mountCandidate(page, adapter, "ru");
      const actual = await captureStates(page);
      for (let i = 0; i < actual.length; i++) {
        if (!actual[i].png.equals(expected[i].png)) {
          await writeFile(info.outputPath(`${adapter}-${actual[i].name}-expected.png`), expected[i].png);
          await writeFile(info.outputPath(`${adapter}-${actual[i].name}-actual.png`), actual[i].png);
        }
        expect(actual[i].png.equals(expected[i].png), `${adapter}: reduced-motion ${actual[i].name}`).toBe(true);
      }
    }
    await page.close();
  });
}
