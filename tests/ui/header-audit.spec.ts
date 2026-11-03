import { expect, test, type Page } from "@playwright/test";

async function doubleHeaderText(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  // Match Firefox's native text-only zoom without changing viewport or boxes.
  await page.locator("header").evaluate(header => {
    const sizes = Array.from(header.querySelectorAll<HTMLElement>("*")).map(node => [node, parseFloat(getComputedStyle(node).fontSize) * 2] as const);
    for (const [node, size] of sizes) node.style.fontSize = `${size}px`;
  });
}

test("enlarged mobile navigation keeps source text and icon inside the panel", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 390, height: 400 });
  await page.goto("/home.html?language=ru");
  await doubleHeaderText(page);
  const menu = page.locator(".tmc-mobile-menu");
  await menu.locator(":scope > summary").click();
  const source = menu.locator(".tmc-source-link");
  await source.focus();
  await expect(source.locator("span")).toBeInViewport({ ratio: 1 });
  await expect(source.locator("svg")).toBeInViewport({ ratio: 1 });
});

test("standard header actions wrap within the viewport with enlarged text", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto("/react.html");
  await page.getByRole("button", { name: "Пересоздать хэдер", exact: true }).click();
  await expect(page.locator("header")).toHaveCount(0);
  await page.evaluate(async () => {
    const reactPath = "/node_modules/.vite/deps/react.js", domPath = "/node_modules/.vite/deps/react-dom_client.js", uiPath = "/src/react/index.tsx";
    const React = (await import(reactPath)).default;
    const { createRoot } = (await import(domPath)).default;
    const { Header, Button, ThemeProvider } = await import(uiPath);
    const root = document.createElement("div");
    document.body.prepend(root);
    createRoot(root).render(React.createElement(ThemeProvider, null, React.createElement(Header, {
      brand: { href: "#home", animation: { fitWordmarkToHeader: true } },
      items: [{ id: "home", href: "#home", label: "Главная" }],
      actions: React.createElement(Button, null, "Настройки команды"),
    })));
  });
  await expect(page.locator("header")).toHaveCount(1);
  await doubleHeaderText(page);
  for (const width of [320, 280]) {
    await page.setViewportSize({ width, height: 800 });
    await expect.poll(() => page.locator("header").evaluate(header =>
      Array.from(header.querySelectorAll(".tmc-header, .tmc-brand, .tmc-header-actions, .tmc-button")).every(node => {
        const box = node.getBoundingClientRect();
        return box.left >= 0 && box.right <= innerWidth;
      }),
    )).toBe(true);
  }
});

test("home header fits the expanded wordmark around navigation with custom text spacing", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1121, height: 800 });
  await page.goto("/home.html?language=ru");
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: "* { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important }" });
  await page.locator(".tmc-brand__wordmark").hover();
  await expect.poll(() => page.evaluate(() => {
    const wordmark = document.querySelector(".tmc-brand__wordmark")!.getBoundingClientRect();
    const navigation = document.querySelector(".tmc-nav a")!.getBoundingClientRect();
    const theme = document.querySelector(".tmc-theme-toggle")!.getBoundingClientRect();
    const header = document.querySelector(".tmc-header")!.getBoundingClientRect();
    return Math.max(wordmark.right - navigation.left, theme.right - header.right);
  })).toBeLessThanOrEqual(0);
});

test("expanded home wordmark remains clickable beyond its reserved width", async ({ page, baseURL }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1200, height: 800 });
  await page.goto("/home.html?language=en");
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: "* { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important }" });
  const wordmark = page.locator(".tmc-brand__wordmark");
  const reservedWidth = (await page.locator(".tmc-brand__wordmark-stage").boundingBox())!.width;
  await wordmark.hover();
  await expect(wordmark).toHaveAttribute("data-hover-expanded", "true");
  await expect.poll(() => wordmark.evaluate(node => node.getBoundingClientRect().width)).toBeGreaterThan(reservedWidth);
  const box = (await wordmark.boundingBox())!;
  await page.mouse.move(box.x + box.width - 12, box.y + box.height / 2);
  await expect(wordmark).toHaveAttribute("data-hover-expanded", "true");
  await page.mouse.click(box.x + box.width - 12, box.y + box.height / 2);
  await expect(page).toHaveURL(new URL("/", baseURL!).href);
});

test("the brand focus outline encloses all expanded text with custom spacing", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  await page.goto("/home.html?language=ru");
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: "* { line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important }" });
  const wordmark = page.locator(".tmc-brand__wordmark");
  const reserved = (await page.locator(".tmc-brand__wordmark-stage").boundingBox())!.width;
  await wordmark.hover();
  await expect.poll(() => wordmark.evaluate(node => node.getBoundingClientRect().width)).toBeGreaterThan(reserved);
  await page.keyboard.press("Tab");
  await expect(page.locator(".tmc-brand")).toBeFocused();
  await expect.poll(() => page.locator(".tmc-brand").evaluate(brand => {
    const text = brand.querySelector(".tmc-brand__wordmark")!.getBoundingClientRect();
    return text.right - brand.getBoundingClientRect().right;
  })).toBeLessThanOrEqual(0);
});

for (const [adapter, path, languageOnly] of [
  ["React", "/react.html", "/react.html?language-only"],
  ["Hugo", "/.runtime/hugo/", "/.runtime/hugo/language-only/"],
]) {
  test(`${adapter} keeps enlarged language abbreviations on one line`, async ({ page }) => {
    for (const [url, width] of [[path, 1440], [languageOnly, 390]] as const) {
      await page.setViewportSize({ width, height: 800 });
      await page.goto(url);
      await doubleHeaderText(page);
      const summary = page.locator(".tmc-language-switcher > summary:visible");
      await expect(summary).toBeInViewport({ ratio: 1 });
      await expect.poll(() => summary.locator("span").evaluate(node => {
        const text = document.createRange();
        text.selectNodeContents(node);
        return text.getClientRects().length;
      })).toBe(1);
      await expect.poll(() => page.locator("header").evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(0);
    }
  });

  test(`${adapter} resets languages only at the actual header breakpoint`, async ({ page }) => {
    await page.setViewportSize({ width: 1040, height: 800 });
    await page.goto(path);
    const menu = page.locator(".tmc-mobile-menu");
    const mobile = page.locator(".tmc-language-switcher--mobile");
    await menu.locator(":scope > summary").click();
    await mobile.locator("summary").click();
    for (const width of [1041, 1120]) {
      await page.setViewportSize({ width, height: 800 });
      await expect(mobile).toHaveAttribute("open", "");
      await expect(mobile.locator("a")).toBeVisible();
    }
    await page.setViewportSize({ width: 1121, height: 800 });
    await expect(menu).not.toHaveAttribute("open");
    await expect(mobile).not.toHaveAttribute("open");
    const desktop = page.locator(".tmc-language-switcher--desktop");
    await desktop.locator("summary").focus();
    await page.keyboard.press("Enter");
    await expect(desktop).toHaveAttribute("open", "");
    await page.setViewportSize({ width: 1120, height: 800 });
    await expect(desktop).not.toHaveAttribute("open");
    await page.setViewportSize({ width: 1121, height: 800 });
    await expect(desktop).not.toHaveAttribute("open");
  });

  test(`${adapter} closes languages when keyboard focus leaves the control`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto(languageOnly);
    const language = page.locator(".tmc-language-switcher");
    await language.locator("summary").focus();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Tab");
    await expect(language.locator("a")).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.locator("header .tmc-theme-toggle")).toBeFocused();
    await expect(language).not.toHaveAttribute("open");
  });

  for (const [variant, variantPath, selector] of [
    ["desktop", path, ".tmc-language-switcher--desktop"],
    ["standalone", languageOnly, ".tmc-language-switcher--standalone"],
  ]) {
    test(`${adapter} preserves keyboard language selection after ${variant} hover ends`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 800 });
      await page.goto(variantPath);
      test.skip(!await page.evaluate(() => matchMedia("(hover: hover) and (pointer: fine)").matches), "Requires a hover pointer");
      const language = page.locator(selector);
      const summary = language.locator("summary");
      const option = language.locator("a").first();
      await summary.hover();
      await expect(language).toHaveAttribute("open", "");
      await summary.focus();
      await page.keyboard.press("Tab");
      await expect(option).toBeFocused();
      await page.mouse.move(8, 790);
      await expect(language).toHaveAttribute("open", "");
      await expect(option).toBeFocused();
      const href = await option.getAttribute("href");
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(new URL(href!, page.url()).href);
      await expect(language).not.toHaveAttribute("open");
    });
  }

  test(`${adapter} fits full language labels inside the popup and viewport`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto(languageOnly);
    const language = page.locator(".tmc-language-switcher");
    const option = language.locator("a").first();
    for (const label of ["Русский", "Deutsch"]) {
      await option.evaluate((node, text) => { node.textContent = text; }, label);
      await language.locator("summary").focus();
      await page.keyboard.press("Enter");
      await expect.poll(() => option.evaluate(node => {
        const range = document.createRange();
        range.selectNodeContents(node);
        const text = range.getBoundingClientRect();
        const panel = node.parentElement!.getBoundingClientRect();
        return text.left >= panel.left && text.right <= panel.right;
      })).toBe(true);
      await page.keyboard.press("Escape");
    }
    await option.evaluate(node => { node.textContent = "Português (Brasil)"; });
    await language.evaluate(node => {
      document.body.append(node);
      node.style.cssText += ";position:absolute;top:150px";
    });
    for (const edge of ["left", "right"]) {
      await language.evaluate((node, edge) => {
        node.style.left = edge === "left" ? "24px" : "auto";
        node.style.right = edge === "right" ? "24px" : "auto";
      }, edge);
      await language.locator("summary").focus();
      await page.keyboard.press("Enter");
      const panel = language.locator(".tmc-language-switcher__list");
      await expect(panel).toBeInViewport({ ratio: 1 });
      await option.focus();
      await expect(option).toBeInViewport({ ratio: 1 });
      await page.setViewportSize({ width: 320, height: 800 });
      await expect(panel).toBeInViewport({ ratio: 1 });
      await page.keyboard.press("Escape");
    }
  });

  test(`${adapter} shows an icon when menu text is empty`, async ({ page }) => {
    await page.setViewportSize({ width: 900, height: 800 });
    await page.goto(path);
    const menu = page.locator(".tmc-mobile-menu");
    await menu.locator(".tmc-mobile-menu__label").evaluate(node => { node.textContent = ""; });
    await expect(menu.locator(".tmc-mobile-menu__icon")).toBeVisible();
    await menu.locator(":scope > summary").click();
    await expect(menu).toHaveAttribute("open", "");
  });

  test(`${adapter} reflows when only header text is enlarged`, async ({ page }) => {
    for (const width of [1121, 381, 280]) {
      await page.setViewportSize({ width, height: 800 });
      await page.goto(adapter === "React" ? "/home.html?language=ru" : path);
      await doubleHeaderText(page);
      await expect.poll(() => page.locator("header").evaluate(header => {
        const bounds = header.querySelector(".tmc-header")!.getBoundingClientRect();
        const actions = header.querySelector(".tmc-header-actions")!.getBoundingClientRect();
        return Math.max(actions.right - bounds.right, header.scrollWidth - header.clientWidth);
      })).toBeLessThanOrEqual(0);
    }
  });

  test(`${adapter} lets oversized headers scroll and restores sticky layout when space returns`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 280, height: 301 });
    await page.goto(adapter === "React" ? "/home.html?language=ru" : path);
    await doubleHeaderText(page);
    await page.addStyleTag({ content: ".tmc-header-shell * {line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important}" });
    const header = page.locator(".tmc-header-shell");
    for (const height of [301, 320, 350]) {
      await page.setViewportSize({ width: 280, height });
      await expect(header).toHaveCSS("position", "relative");
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      await page.evaluate(() => window.scrollTo({ top: 500, behavior: "instant" }));
      await expect.poll(() => header.evaluate(node => node.getBoundingClientRect().bottom)).toBeLessThanOrEqual(0);
    }
    await page.setViewportSize({ width: 280, height: 1000 });
    await expect(header).toHaveCSS("position", "sticky");
  });

  for (const reducedMotion of ["reduce", "no-preference"] as const) {
    test(`${adapter} keeps nested language geometry local with motion ${reducedMotion}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion });
      await page.setViewportSize({ width: 390, height: 400 });
      await page.goto(path);
      const menu = page.locator(".tmc-mobile-menu");
      await menu.evaluate(node => { node.style.cssText = "position:absolute;left:60px;top:60px"; });
      await menu.locator(":scope > summary").focus();
      await page.keyboard.press("Enter");
      const panel = menu.locator(":scope > nav");
      await expect(panel).toBeInViewport({ ratio: 1 });
      const language = menu.locator(".tmc-language-switcher");
      await language.locator("summary").focus();
      await page.keyboard.press("Enter");
      await expect(language.locator(".tmc-language-switcher__list")).toHaveCSS("translate", "none");
      await language.locator("a").focus();
      await page.evaluate(() => window.scrollBy(0, 1));
      for (let frame = 0; frame < 10; frame++) {
        await page.evaluate(() => new Promise(requestAnimationFrame));
        await expect(panel).toBeInViewport({ ratio: 1 });
        await expect(language.locator("a")).toBeInViewport({ ratio: 1 });
      }
    });
  }

  test(`${adapter} lets the pointer select a language when the menu opens over the brand`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 381, height: 301 });
    await page.goto(adapter === "React" ? "/home.html?language=ru" : path);
    await doubleHeaderText(page);
    await page.addStyleTag({ content: ".tmc-header-shell * {line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important}" });
    const menu = page.locator(".tmc-mobile-menu");
    await menu.locator(":scope > summary").focus();
    await page.keyboard.press("Enter");
    const language = menu.locator(".tmc-language-switcher");
    await language.locator("summary").focus();
    await page.keyboard.press("Enter");
    const option = language.locator("a");
    await option.focus();
    await expect(menu.locator(":scope > nav")).toHaveAttribute("data-tmc-above", "");
    await expect.poll(() => option.evaluate(node => {
      const box = node.getBoundingClientRect();
      return node.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
    })).toBe(true);
    const href = new URL((await option.getAttribute("href"))!, page.url()).href;
    const box = (await option.boundingBox())!;
    await Promise.all([page.waitForURL(href), page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)]);
    expect(await page.evaluate(() => localStorage.getItem("tmc-language"))).toBe("en");
  });

  test(`${adapter} keeps long language lists reachable in a sticky header and near the footer`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 390, height: 400 });
    await page.goto(languageOnly);
    const language = page.locator(".tmc-language-switcher");
    await language.evaluate(node => {
      document.querySelector<HTMLElement>("main")!.style.minHeight = "2000px";
      const panel = node.querySelector(".tmc-language-switcher__list")!;
      for (const code of ["de", "fr", "es", "it", "pt", "nl", "pl", "uk", "cs", "ja", "ko", "zh", "tr", "fi"]) {
        const link = document.createElement("a");
        link.className = "tmc-language-switcher__option";
        link.href = `#${code}`;
        link.lang = code;
        link.textContent = code.toUpperCase();
        panel.append(link);
      }
    });
    for (const footer of [false, true]) {
      if (footer) await language.evaluate(node => {
        document.body.append(node);
        node.style.cssText += ";position:fixed;left:24px;bottom:4px";
      });
      await language.locator("summary").focus();
      await page.keyboard.press("Enter");
      const last = language.locator("a").last();
      await last.focus();
      await expect(last).toBeFocused();
      await expect(last).toBeInViewport({ ratio: 1 });
      await expect(language.locator(".tmc-language-switcher__list")).toBeInViewport({ ratio: 1 });
      await page.keyboard.press("Escape");
    }
  });

  test(`${adapter} keeps the next focused link visible when languages collapse`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 900, height: 180 });
    await page.goto(path);
    const menu = page.locator(".tmc-mobile-menu");
    await menu.locator(":scope > summary").focus();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    const language = menu.locator(".tmc-language-switcher");
    await expect(language).toHaveAttribute("open", "");
    await page.keyboard.press("Tab");
    await expect(language.locator("a")).toBeFocused();
    await expect(language.locator("a")).toBeInViewport({ ratio: 1 });
    await page.keyboard.press("Tab");
    const first = menu.locator(".tmc-mobile-menu__links a").first();
    await expect(first).toBeFocused();
    await expect(language).not.toHaveAttribute("open");
    await expect(first).toBeInViewport({ ratio: 1 });
  });

  test(`${adapter} keeps the theme state and eye visible in forced colors`, async ({ page }) => {
    await page.emulateMedia({ forcedColors: "active", colorScheme: "light", reducedMotion: "reduce" });
    await page.addInitScript(() => localStorage.setItem("tmc-theme", "light"));
    await page.goto(path);
    const theme = page.locator("header .tmc-theme-toggle");
    await theme.hover();
    const before = await theme.screenshot({ animations: "disabled" });
    await theme.click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    expect((await theme.screenshot({ animations: "disabled" })).equals(before), "selected theme must remain visible").toBe(false);
    const eye = page.locator("header .tmc-eye");
    await expect(eye.locator(".tmc-eye__clip")).toHaveCSS("clip-path", "none");
    const visible = await eye.screenshot({ animations: "disabled" });
    const hidden = await page.addStyleTag({ content: ".tmc-eye__surface, .tmc-eye__pupil { visibility: hidden !important }" });
    expect((await eye.screenshot({ animations: "disabled" })).equals(visible), "the eye must paint visible pixels").toBe(false);
    await hidden.evaluate(node => node.parentNode?.removeChild(node));
  });

  test(`${adapter} keeps every mobile navigation item reachable in a short viewport`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    for (const viewport of [{ width: 900, height: 180 }, { width: 320, height: 180 }, { width: 320, height: 301 }]) {
      await page.setViewportSize(viewport);
      await page.goto(path);
      await page.locator(".tmc-mobile-menu > summary").click();
      const last = page.locator(".tmc-mobile-menu__links a").last();
      await last.scrollIntoViewIfNeeded();
      await expect(last).toBeInViewport({ ratio: 1 });
      const href = await last.getAttribute("href");
      await last.click();
      await expect(page).toHaveURL(new URL(href!, page.url()).href);
    }
  });
}
