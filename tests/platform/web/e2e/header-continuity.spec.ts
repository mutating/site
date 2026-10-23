import { openLanguages, openUserMenu, selectLanguage } from "./header-controls";
import type { Page } from "@playwright/test";
import { API, expect, login, password, test } from "./fixtures";

async function holdSession(page: Page, method: string) {
  let release!: () => void;
  let received!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const requested = new Promise<void>((resolve) => { received = resolve; });
  await page.route(`${API}/auth/session`, async (route) => {
    if (route.request().method() !== method) return route.continue();
    const response = await route.fetch();
    received();
    await held;
    await route.fulfill({ response });
  });
  return { release, requested };
}

for (const transition of ["session to login", "session to cabinet", "sign in", "sign out"] as const) {
  for (const control of ["language", "theme"] as const) {
    for (const input of ["mouse", "keyboard"] as const) {
      test(`${control} ${input} activation survives ${transition}`, async ({ page, account }) => {
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        if (transition === "session to cabinet" || transition === "sign out") {
          await login(page, account);
        } else if (transition === "sign in") {
          await page.goto("login");
          await page.getByLabel("Email или ник").fill(account.email);
          await page.getByLabel("Пароль", { exact: true }).fill(password);
        }

        const method = transition === "sign in" ? "POST" : transition === "sign out" ? "DELETE" : "GET";
        const held = await holdSession(page, method);
        try {
          if (transition === "sign in") {
            await page.getByRole("button", { name: "Войти", exact: true }).click();
          } else if (transition === "sign out") {
            await (await openUserMenu(page)).getByRole("menuitem", { name: "Выйти", exact: true }).click();
          } else {
            await page.goto(transition === "session to cabinet" ? "profile" : "login");
            await expect(page.locator(".boot-state .loading")).toBeVisible();
          }
          await held.requested;
          const button = control === "language"
            ? (await openLanguages(page)).locator('[data-language-option="en"]')
            : page.locator(".tmc-theme-toggle");
          if (input === "mouse") {
            await button.hover();
            const bounds = (await button.boundingBox())!;
            await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
            await page.mouse.down();
          } else {
            await button.focus();
            await page.keyboard.down(control === "language" ? "Enter" : "Space");
          }
          held.release();
          if (transition === "session to login" || transition === "sign out") {
            await expect(page.getByRole("heading", { name: /^(С возвращением\.|Welcome back\.)$/ })).toBeVisible();
          } else {
            await expect(page.getByRole("navigation", { name: /^(Навигация кабинета|Account navigation)$/ })).toBeVisible();
          }
          if (input === "mouse") await page.mouse.up();
          else await page.keyboard.up(control === "language" ? "Enter" : "Space");

          await expect(page.locator("html")).toHaveAttribute(
            control === "language" ? "lang" : "data-theme",
            control === "language" ? "en" : "light",
          );
          await expect(page.getByRole("banner")).toHaveCount(1);
          if (input === "keyboard") {
            if (control === "theme") await expect(button).toBeFocused();
            else await expect(page.locator(".tmc-language-switcher--desktop > summary:visible, .tmc-mobile-menu > summary:visible")).toBeFocused();
          }
          // A second activation must still produce exactly one change.
          if (control === "language") await selectLanguage(page, "ru");
          else await button.click();
          await expect(page.locator("html")).toHaveAttribute(
            control === "language" ? "lang" : "data-theme",
            control === "language" ? "ru" : "dark",
          );
          expect(errors).toEqual([]);
        } finally {
          held.release();
        }
      });
    }
  }
}

test("a native touch held during session resolution activates each header control once", async ({ page, account, browserName, isMobile }) => {
  // Chromium exposes separate native touch start/end through CDP; tap() is atomic.
  test.skip(browserName !== "chromium" || !isMobile, "Requires the mobile Chromium touch protocol");
  const touch = await page.context().newCDPSession(page);
  for (const authenticated of [false, true]) {
    if (authenticated) await login(page, account);
    for (const control of ["language", "theme"] as const) {
      const held = await holdSession(page, "GET");
      try {
        await page.goto(authenticated ? "profile" : "login");
        await expect(page.locator(".boot-state .loading")).toBeVisible();
        const button = control === "language"
          ? (await openLanguages(page, "touch")).locator('[data-language-option="en"]')
          : page.locator(".tmc-theme-toggle");
        if (control === "language") await expect.poll(() => page.locator(".tmc-language-switcher:visible").evaluate(element => element.getAnimations({ subtree: true }).some(animation => animation.playState === "running"))).toBe(false);
        const bounds = (await button.boundingBox())!;
        await touch.send("Input.dispatchTouchEvent", {
          type: "touchStart",
          touchPoints: [{ x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 }],
        });
        held.release();
        if (authenticated) await expect(page.getByRole("navigation", { name: "Навигация кабинета" })).toBeVisible();
        else await expect(page.getByRole("heading", { name: /^(С возвращением\.|Welcome back\.)$/ })).toBeVisible();
        await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        await expect(page.locator("html")).toHaveAttribute(
          control === "language" ? "lang" : "data-theme",
          control === "language" ? "en" : "light",
        );
        if (authenticated) await expect(page.locator(".user-menu .tmc-square-button")).toBeVisible();
        if (control === "language") await selectLanguage(page, "ru", "touch");
        else await button.tap();
        await expect(page.locator("html")).toHaveAttribute(
          control === "language" ? "lang" : "data-theme",
          control === "language" ? "ru" : "dark",
        );
      } finally {
        held.release();
        await page.unroute(`${API}/auth/session`);
      }
    }
  }
  await touch.detach();
});
