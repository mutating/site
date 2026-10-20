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
            await page.getByRole("button", { name: "Выйти", exact: true }).filter({ visible: true }).click();
          } else {
            await page.goto(transition === "session to cabinet" ? "profile" : "login");
            await expect(page.locator(".boot-state .loading")).toBeVisible();
          }
          await held.requested;
          const button = page.getByRole("banner").getByRole("button", {
            name: control === "language" ? /^(Выбрать язык|Choose language)$/ : /^(Переключить тему|Toggle color theme)$/,
            exact: true,
          });
          if (input === "mouse") {
            const bounds = (await button.boundingBox())!;
            await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
            await page.mouse.down();
          } else {
            await button.focus();
            await page.keyboard.down("Space");
          }
          held.release();
          if (transition === "session to login" || transition === "sign out") {
            await expect(page.getByRole("heading", { name: "С возвращением." })).toBeVisible();
          } else {
            await expect(page.getByRole("navigation", { name: "Навигация кабинета" })).toBeVisible();
          }
          if (input === "mouse") await page.mouse.up();
          else await page.keyboard.up("Space");

          await expect(page.locator("html")).toHaveAttribute(
            control === "language" ? "lang" : "data-theme",
            control === "language" ? "en" : "light",
          );
          await expect(page.getByRole("banner")).toHaveCount(1);
          if (input === "keyboard") await expect(button).toBeFocused();
          // A second activation must still produce exactly one change.
          await button.click();
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

test("a native touch held during session resolution activates each header control once", async ({ page, browserName, isMobile }) => {
  // Chromium exposes separate native touch start/end through CDP; tap() is atomic.
  test.skip(browserName !== "chromium" || !isMobile, "Requires the mobile Chromium touch protocol");
  const touch = await page.context().newCDPSession(page);
  for (const control of ["language", "theme"] as const) {
    const held = await holdSession(page, "GET");
    try {
      await page.goto("login");
      await expect(page.locator(".boot-state .loading")).toBeVisible();
      const button = page.getByRole("banner").getByRole("button", {
        name: control === "language" ? /^(Выбрать язык|Choose language)$/ : /^(Переключить тему|Toggle color theme)$/,
      });
      const bounds = (await button.boundingBox())!;
      await touch.send("Input.dispatchTouchEvent", {
        type: "touchStart",
        touchPoints: [{ x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 }],
      });
      held.release();
      await expect(page.getByRole("heading", { name: "С возвращением." })).toBeVisible();
      await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await expect(page.locator("html")).toHaveAttribute(
        control === "language" ? "lang" : "data-theme",
        control === "language" ? "en" : "light",
      );
      await button.tap();
      await expect(page.locator("html")).toHaveAttribute(
        control === "language" ? "lang" : "data-theme",
        control === "language" ? "ru" : "dark",
      );
    } finally {
      held.release();
      await page.unroute(`${API}/auth/session`);
    }
  }
  await touch.detach();
});
