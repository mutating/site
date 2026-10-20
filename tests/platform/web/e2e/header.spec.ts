import { selectLanguage, languageControl } from "./header-controls";
import { API, expect, login, test } from "./fixtures";

for (const route of ["login", "register"]) {
  test(`header switches language in place and preserves the ${route} form`, async ({ page }) => {
    await page.goto(`${route}?source=header-test#form`);
    const identifier = page.locator(`input[name="${route === "login" ? "login" : "email"}"]`);
    await identifier.fill("header@example.com");
    await page.locator('input[name="password"]').fill("unsaved header password");
    if (route === "register") await page.locator('input[name="first_name"]').fill("Черновик");
    const originalURL = page.url();
    await page.evaluate(() => { document.body.dataset.headerTestDocument = "same-document"; });
    const header = page.getByRole("banner");
    await expect(header.getByRole("link", { name: "The Mutating Company", exact: true })).toHaveAttribute("href", "https://mutating.tech/ru/");
    await selectLanguage(page);
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page).toHaveTitle("Account — The Mutating Company");
    await expect(page.locator(".tmc-language-switcher > summary span")).toHaveText(["EN", "EN"]);
    await expect(header.getByRole("link", { name: "The Mutating Company", exact: true })).toHaveAttribute("href", "https://mutating.tech/");
    await expect(identifier).toHaveValue("header@example.com");
    await expect(page.locator('input[name="password"]')).toHaveValue("unsaved header password");
    if (route === "register") await expect(page.locator('input[name="first_name"]')).toHaveValue("Черновик");
    expect(page.url()).toBe(originalURL);
    await expect(page.locator("body")).toHaveAttribute("data-header-test-document", "same-document");
    expect(await page.evaluate(() => localStorage.getItem("tmc-language"))).toBe("en");
    await selectLanguage(page, "ru", "keyboard");
    await expect(page.locator("html")).toHaveAttribute("lang", "ru");
    await expect(page).toHaveTitle("Личный кабинет — The Mutating Company");
    await expect(identifier).toHaveValue("header@example.com");
    expect(page.url()).toBe(originalURL);
  });
}

test("theme and language support keyboard input and survive auth route changes", async ({ page }) => {
  await page.goto("login");
  await expect(page.getByRole("heading", { name: "С возвращением." })).toBeVisible();
  const theme = page.getByRole("banner").getByRole("button", { name: "Переключить тему", exact: true });
  await theme.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await page.evaluate(() => localStorage.getItem("tmc-theme"))).toBe("light");
  await page.keyboard.press("Space");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.keyboard.press("Enter");
  await selectLanguage(page, "en", "keyboard");
  await page.getByRole("link", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Let’s get started." })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.locator(".tmc-language-switcher > summary span")).toHaveText(["EN", "EN"]);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Let’s get started." })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Toggle color theme", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("saved preferences override browser defaults before rendering the header", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("header-preferences-seeded")) {
      localStorage.setItem("tmc-theme", "dark");
      localStorage.setItem("tmc-language", "en");
      sessionStorage.setItem("header-preferences-seeded", "yes");
    }
  });
  await page.goto("login");
  await expect(page.getByRole("heading", { name: "Welcome back." })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Toggle color theme", exact: true }).click();
  await selectLanguage(page);
  await page.reload();
  await expect(page.getByRole("heading", { name: "С возвращением." })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("header controls still work when preference storage is unavailable", async ({ page }) => {
  await page.addInitScript(() => {
    for (const method of ["getItem", "setItem"] as const) {
      Storage.prototype[method] = () => { throw new DOMException("Storage denied", "SecurityError"); };
    }
  });
  await page.goto("login");
  await expect(page.getByRole("heading", { name: "С возвращением." })).toBeVisible();
  await selectLanguage(page);
  await page.getByRole("button", { name: "Toggle color theme", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("link", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Let’s get started." })).toBeVisible();
  await page.getByRole("button", { name: "Toggle color theme", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("system changes follow the site policy and other-tab changes take effect on reload", async ({ page }) => {
  await page.goto("login");
  await expect(page.getByRole("heading", { name: "С возвращением." })).toBeVisible();
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Переключить тему", exact: true }).click();
  const sibling = await page.context().newPage();
  await sibling.goto("login");
  await page.evaluate(() => {
    window.addEventListener("storage", function changed(event) {
      if (event.key !== "tmc-language") return;
      window.removeEventListener("storage", changed);
      document.body.dataset.headerStorageReceived = "true";
    });
  });
  await sibling.evaluate(() => {
    localStorage.setItem("tmc-theme", "light");
    localStorage.setItem("tmc-language", "en");
  });
  await page.bringToFront();
  await expect(page.locator("body")).toHaveAttribute("data-header-storage-received", "true");
  await expect(page.locator("html")).toHaveAttribute("lang", "ru");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await sibling.close();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Welcome back." })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("header controls remain usable while session loading fails and recovers", async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`${API}/auth/session`, async (route) => {
    await held;
    await route.fulfill({ status: 503, json: { detail: "Header session test" } });
  });
  await page.goto("login");
  await expect(page.locator(".boot-state .loading")).toBeVisible();
  await selectLanguage(page);
  await page.getByRole("button", { name: "Toggle color theme", exact: true }).click();
  release();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.unroute(`${API}/auth/session`);
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back." })).toBeVisible();
  await page.getByRole("button", { name: "Toggle color theme", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("the brand navigates to the localized website with native link behavior", async ({ page, browserName }) => {
  await page.context().route("https://mutating.tech/**", (route) => route.fulfill({ contentType: "text/html", body: "<title>Main website</title>" }));
  await page.goto("login");
  await expect(page.getByRole("heading", { name: "С возвращением." })).toBeVisible();
  const brand = page.getByRole("banner").getByRole("link", { name: "The Mutating Company", exact: true });
  await expect(brand).toHaveAttribute("href", "https://mutating.tech/ru/");
  await brand.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL("https://mutating.tech/ru/");
  await page.goBack();
  await expect(page.getByRole("heading", { name: "С возвращением." })).toBeVisible();
  await selectLanguage(page);
  // Headless WebKit does not implement the browser UI's middle-click tab action.
  if (browserName === "webkit") {
    await brand.click();
    await expect(page).toHaveURL("https://mutating.tech/");
    return;
  }
  const popupPromise = page.context().waitForEvent("page");
  await brand.click({ button: "middle" });
  const popup = await popupPromise;
  await expect(popup).toHaveURL("https://mutating.tech/");
  await expect(page).toHaveURL("http://127.0.0.1:18101/login");
  await popup.close();
});

test("authenticated header changes preserve the route, draft and session", async ({ page, account }) => {
  await login(page, account);
  await page.goto("profile?source=header-test#draft");
  await page.getByLabel("Имя", { exact: true }).fill("Unsaved header draft");
  const originalURL = page.url();
  await selectLanguage(page);
  await page.getByRole("button", { name: "Toggle color theme", exact: true }).click();
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue("Unsaved header draft");
  expect(page.url()).toBe(originalURL);
  await page.getByRole("navigation", { name: "Account navigation" }).getByRole("link", { name: "Power Cells", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:18101/power-cells");
  await expect(page.getByRole("heading", { name: "Power Cells.", exact: true })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(originalURL);
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue(account.first_name);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.getByRole("banner")).toHaveCount(1);
  await expect(languageControl(page)).toHaveCount(1);
  await page.getByRole("button", { name: "Toggle color theme", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("header stays reachable while scrolling and resizing without horizontal overflow", async ({ page }) => {
  await page.goto("register");
  await expect(page.getByRole("heading", { name: "Начнём знакомство." })).toBeVisible();
  for (const width of [320, 390, 540, 800, 900, 1120, 1440]) {
    await page.setViewportSize({ width, height: 600 });
    const header = page.getByRole("banner");
    await expect(languageControl(page)).toBeInViewport();
    await expect(header.getByRole("button", { name: "Переключить тему", exact: true })).toBeInViewport();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await expect(header.getByRole("button", { name: "Переключить тему", exact: true })).toBeInViewport();
    await page.evaluate(() => window.scrollTo(0, 0));
  }
});
