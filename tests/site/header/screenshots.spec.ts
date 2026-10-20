import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const epoch = new Date("2026-10-16T09:00:00Z");
const referenceDirectory = resolve(import.meta.dirname, "reference");
const manifest = JSON.parse(await readFile(resolve(referenceDirectory, "manifest.json"), "utf8"));
type Shot = { name: string; png: Buffer };

async function prepare(page: Page, reference: boolean, language: string, theme: string, width: number, reducedMotion = false) {
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

async function selector(page: Page, name: string) {
  return page.evaluate(name => (window as any).headerClass(name), name);
}

async function capture(page: Page, name: string, height = 160, animationTime = 1000): Promise<Shot> {
  await page.evaluate(time => {
    document.body.getBoundingClientRect();
    for (const animation of document.getAnimations()) { animation.pause(); animation.currentTime = time; }
  }, animationTime);
  return { name, png: await page.screenshot({ clip: { x: 0, y: 0, width: page.viewportSize()!.width, height }, animations: "allow", caret: "hide" }) };
}

async function compare(expected: Shot[], actual: Shot[], info: TestInfo) {
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

async function states(page: Page): Promise<Shot[]> {
  const width = page.viewportSize()!.width;
  const pictures: Shot[] = [];
  const take = async (name: string, height = 160, time = 1000) => pictures.push(await capture(page, name, height, time));
  const move = async (name: string, x: number, y: number) => {
    await page.mouse.move(x, y);
    await page.clock.runFor(1024);
    await take(name);
  };
  await move("cursor-bottom-left", 1, 999);
  await move("cursor-bottom-right", width - 1, 999);
  await move("cursor-top-right", width - 1, 1);
  await move("cursor-top-left", 1, 1);
  await move("cursor-page-center", width / 2, 500);
  const eye = await page.locator(await selector(page, "animated-eye-logo")).boundingBox();
  const cx = eye!.x + eye!.width / 2;
  const cy = eye!.y + eye!.height / 2;
  await move("cursor-eye-center", cx, cy);
  for (const [name, x, y] of [
    ["left", eye!.x + 1, cy], ["right", eye!.x + eye!.width - 1, cy],
    ["above", cx, eye!.y + 1], ["below", cx, eye!.y + eye!.height - 1],
  ] as const) await move(`cursor-eye-${name}`, x, y);
  await page.mouse.move(width - 1, 999);
  for (const [advance, time] of [[16, 16], [64, 80], [96, 176], [336, 512]]) {
    await page.clock.runFor(advance);
    await take(`gaze-spring-${time}`, 160, time);
  }

  const wordmark = page.locator(await selector(page, "brand__wordmark"));
  const stage = page.locator(await selector(page, "brand__wordmark-stage"));
  const box = (await wordmark.boundingBox())!;
  await page.mouse.move(box.x + 3, box.y + box.height / 2);
  for (const [advance, time] of [[16, 16], [64, 80], [96, 176], [176, 352]]) {
    await page.clock.runFor(advance);
    await take(`wordmark-unfold-${time}`, 160, time);
  }
  const stageBox = (await stage.boundingBox())!;
  await move("cursor-reserved-wordmark-right-edge", stageBox.x + stageBox.width - 2, stageBox.y + stageBox.height / 2);
  const anchors = page.locator(await selector(page, "brand__word-anchor"));
  for (let index = 0; index < 3; index++) {
    const bounds = (await anchors.nth(index).boundingBox())!;
    await move(`cursor-letter-${"TMC"[index]}`, bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  }
  await page.mouse.move(1, 999);
  for (const [advance, time] of [[80, 80], [96, 176], [176, 352]]) {
    await page.clock.runFor(advance);
    await take(`wordmark-fold-${time}`, 160, time);
  }
  const brand = page.locator(await selector(page, "brand"));
  await brand.focus(); await take("programmatic-brand-focus"); await brand.blur();

  const mobile = width <= 1120;
  const menu = page.locator(await selector(page, "mobile-menu"));
  if (mobile) {
    await menu.locator(":scope > summary").click();
    await page.clock.runFor(320); await take("menu-open", 560);
    await menu.locator("nav a").last().hover();
    await page.clock.runFor(320); await take("menu-contact-hover", 560);
  } else {
    const nav = page.locator(await selector(page, "main-nav"));
    for (let index = 0; index < 4; index++) {
      await nav.locator("a").nth(index).hover();
      await page.clock.runFor(320); await take(`navigation-hover-${index}`);
    }
  }
  const language = page.locator(await selector(page, `language-switcher--${mobile ? "mobile" : "desktop"}`));
  await language.locator(":scope > summary").evaluate((node: HTMLElement) => { node.focus(); node.click(); });
  await page.clock.runFor(320); await take("language-open-focus", mobile ? 560 : 280);
  await language.locator("a").first().hover();
  await page.clock.runFor(320); await take("language-option-hover", mobile ? 560 : 280);
  await page.evaluate(() => document.querySelectorAll("details[open]").forEach(node => node.removeAttribute("open")));
  await page.locator(await selector(page, "platform-link")).hover();
  await page.clock.runFor(320); await take("platform-hover");
  const toggle = page.locator(await selector(page, "theme-toggle"));
  await toggle.hover(); await page.clock.runFor(320); await take("theme-hover");
  await toggle.evaluate((node: HTMLElement) => { node.focus(); node.click(); });
  for (const [advance, time] of [[16, 16], [64, 80], [96, 176], [240, 416]]) {
    await page.clock.runFor(advance); await take(`theme-blink-${time}`, 160, time);
  }
  await toggle.blur();
  await page.mouse.move(1, 999);
  await page.evaluate(() => window.scrollTo({ top: 500, behavior: "instant" }));
  await page.clock.runFor(1024); await take("scrolled-eye-white");
  await page.locator(await selector(page, "animated-eye-logo")).hover();
  for (const [advance, time] of [[80, 80], [336, 416]]) {
    await page.clock.runFor(advance); await take(`scrolled-eye-hover-${time}`, 160, time);
  }
  await page.mouse.move(1, 999);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.clock.runFor(1024);
  // The automatic 30-second blink is driven by the real timer, not a mock blink.
  const now = await page.evaluate(() => Date.now());
  const nextBlink = epoch.getTime() + 30_000;
  if (now < nextBlink) await page.clock.runFor(nextBlink - now);
  for (const [advance, time] of [[80, 80], [96, 176], [240, 416]]) {
    await page.clock.runFor(advance); await take(`periodic-blink-${time}`, 160, time);
  }
  await page.evaluate(() => window.dispatchEvent(new Event("tmc-brand-home-activate")));
  await page.clock.runFor(80); await take("home-eye-closing", 160, 80);
  await page.clock.runFor(192); await take("home-eye-held-closed");
  return pictures;
}

for (const width of [1440, 1241, 1240, 1121, 1120, 1041, 1040, 921, 920, 861, 860, 581, 580, 481, 480, 381, 380, 360, 280]) {
  for (const language of ["en", "ru"]) for (const theme of ["light", "dark"]) {
    test(`rest ${language} ${theme} ${width}px`, async ({ context }, info) => {
      const before = await context.newPage(); const after = await context.newPage();
      await prepare(before, true, language, theme, width);
      await prepare(after, false, language, theme, width);
      await compare([await capture(before, "rest")], [await capture(after, "rest")], info);
    });
  }
}
for (const width of [1440, 1120, 1040, 921, 920, 580, 480, 360, 280]) {
  for (const language of ["en", "ru"]) for (const theme of ["light", "dark"]) {
    test(`pointer and controls ${language} ${theme} ${width}px`, async ({ context }, info) => {
      const before = await context.newPage(); const after = await context.newPage();
      await prepare(before, true, language, theme, width);
      const expected = await states(before);
      await prepare(after, false, language, theme, width);
      await compare(expected, await states(after), info);
    });
  }
}
for (const width of [1440, 1120, 360]) {
  test(`reduced motion ${width}px`, async ({ context }, info) => {
    const before = await context.newPage(); const after = await context.newPage();
    await prepare(before, true, "ru", "dark", width, true);
    const expected = await states(before);
    await prepare(after, false, "ru", "dark", width, true);
    await compare(expected, await states(after), info);
  });
}

test.describe("Chromium high contrast", () => {
  test.use({ forcedColors: "active" });
  for (const width of [1440, 375]) {
    for (const language of ["en", "ru"]) for (const theme of ["light", "dark"]) {
      test(`system colors ${language} ${theme} ${width}px`, async ({ context }, info) => {
        const before = await context.newPage(); const after = await context.newPage();
        await prepare(before, true, language, theme, width);
        await prepare(after, false, language, theme, width);
        const expected = [await capture(before, "rest")];
        const actual = [await capture(after, "rest")];
        for (const [page, pictures] of [[before, expected], [after, actual]] as const) {
          await page.keyboard.press("Tab");
          await page.locator(await selector(page, "brand")).focus();
          await page.clock.runFor(320);
          pictures.push(await capture(page, "brand-focus-visible"));
        }
        await compare(expected, actual, info);
      });
    }
  }
});

test("held Enter does not activate a newly focused reload brand in Firefox", async ({ context }) => {
  for (const reference of [true, false]) {
    const page = await context.newPage();
    await prepare(page, reference, "en", "light", 1440);
    await page.evaluate(() => {
      (window as any).headerActivations = 0;
      addEventListener("tmc-brand-home-activate", () => (window as any).headerActivations++);
    });
    await page.keyboard.down("Enter");
    await page.keyboard.press("Tab");
    const brand = page.locator(await selector(page, "brand"));
    await expect(brand).toBeFocused();
    await page.keyboard.down("Enter");
    await page.keyboard.up("Enter");
    expect(await page.evaluate(() => (window as any).headerActivations)).toBe(0);
    await page.close();
  }
});

async function keyboardStates(page: Page): Promise<Shot[]> {
  const pictures: Shot[] = [];
  const mobile = page.viewportSize()!.width <= 1120;
  const take = async (name: string, height = 160) => {
    await page.clock.runFor(320);
    pictures.push(await capture(page, name, height));
  };
  await page.mouse.move(1, 999);
  await page.keyboard.press("Tab");
  await page.locator(await selector(page, "brand")).focus();
  await take("brand-focus-visible");
  if (mobile) {
    const summary = page.locator((await selector(page, "mobile-menu")) + " > summary");
    await summary.focus(); await take("menu-summary-focus-visible");
    await summary.press("Enter"); await take("menu-keyboard-open", 560);
  } else {
    const links = page.locator((await selector(page, "main-nav")) + " a");
    for (let index = 0; index < 4; index++) {
      await links.nth(index).focus(); await take(`navigation-focus-visible-${index}`);
    }
  }
  const language = page.locator(await selector(page, `language-switcher--${mobile ? "mobile" : "desktop"}`));
  const summary = language.locator(":scope > summary");
  await summary.focus(); await take("language-summary-focus-visible", mobile ? 560 : 280);
  await summary.press("Enter"); await take("language-keyboard-open", mobile ? 560 : 280);
  const bounds = (await summary.boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await take("language-summary-hover", mobile ? 560 : 280);
  await page.keyboard.press("Tab");
  await language.locator("a").first().focus();
  await take("language-option-focus-visible", mobile ? 560 : 280);
  await page.evaluate(() => document.querySelectorAll("details[open]").forEach(node => node.removeAttribute("open")));
  await page.mouse.move(1, 999);
  await page.keyboard.press("Tab");
  await page.locator(await selector(page, "theme-toggle")).focus(); await take("theme-focus-visible");
  await page.locator(await selector(page, "platform-link")).focus(); await take("platform-focus-visible");
  return pictures;
}

for (const width of [1440, 1120, 360]) {
  for (const language of ["en", "ru"]) for (const theme of ["light", "dark"]) {
    test(`keyboard focus and language hover ${language} ${theme} ${width}px`, async ({ context }, info) => {
      const before = await context.newPage(); const after = await context.newPage();
      await prepare(before, true, language, theme, width);
      const expected = await keyboardStates(before);
      await prepare(after, false, language, theme, width);
      await compare(expected, await keyboardStates(after), info);
    });
  }
}

for (const [name, declaration] of [
  ["line-height", "line-height:1.5!important"],
  ["font-family", "font-family:Arial!important"],
  ["color", "color:rgb(170,0,0)!important"],
  ["text-spacing", "line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important"],
] as const) {
  for (const width of [1440, 1040, 360]) for (const language of ["en", "ru"]) {
    test(`inherited body typography ${name} ${language} ${width}px`, async ({ context }, info) => {
      const sequences: Shot[][] = [];
      for (const reference of [true, false]) {
        const page = await context.newPage();
        await prepare(page, reference, language, "light", width);
        await page.addStyleTag({ content: `body { ${declaration}; }` });
        await page.clock.runFor(512);
        const pictures = [await capture(page, "rest")];
        await page.locator(await selector(page, "brand__wordmark")).hover();
        await page.clock.runFor(512);
        pictures.push(await capture(page, "brand-hover"));
        const mobile = width <= 1120;
        if (mobile) await page.locator((await selector(page, "mobile-menu")) + " > summary").click();
        const navigation = mobile ? "mobile-menu__links" : "main-nav";
        await page.locator((await selector(page, navigation)) + " a").first().hover();
        await page.clock.runFor(512);
        pictures.push(await capture(page, "navigation-hover", mobile ? 560 : 160));
        const languageMenu = page.locator(await selector(page, `language-switcher--${mobile ? "mobile" : "desktop"}`));
        await languageMenu.locator("summary").evaluate((element: HTMLElement) => { element.focus(); element.click(); });
        await page.clock.runFor(512);
        pictures.push(await capture(page, "language-open", mobile ? 560 : 280));
        sequences.push(pictures);
        await page.close();
      }
      await compare(sequences[0], sequences[1], info);
    });
  }
}
