import { API, expect, startSession, test } from "./fixtures";
import type { Page } from "@playwright/test";

const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=", "base64");
const avatar = { name: "avatar.png", mimeType: "image/png", buffer: png };
const cors = {
  "Access-Control-Allow-Origin": "http://127.0.0.1:18101",
  "Access-Control-Allow-Credentials": "true",
};

function deferred() {
  let release!: () => void;
  const pending = new Promise<void>(resolve => release = resolve);
  return { pending, release };
}

async function leavePersonal(page: Page) {
  await page.getByRole("tab", { name: "Аккаунт", exact: true }).click();
  await expect(page.getByRole("button", { name: "Удалить аккаунт", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "API-токены", exact: true }).click();
  await expect(page.getByRole("button", { name: "Создать токен", exact: true })).toBeEnabled();
}

for (const origin of ["personal", "account"] as const) {
  test(`a real issued secret is not restored after history returns from ${origin}`, async ({ page, account, request }) => {
    let posts = 0;
    page.on("request", request => {
      if (request.method() === "POST" && request.url() === `${API}/api-tokens`) posts++;
    });
    await startSession(page, account);
    await page.goto(origin === "personal" ? "/profile" : "/profile?section=account");
    await page.getByRole("tab", { name: "API-токены", exact: true }).click();
    const create = page.getByRole("button", { name: "Создать токен", exact: true });
    await expect(create).toBeEnabled();
    await create.click();
    const dialog = page.getByRole("dialog");
    const name = "Токен перед переходом";
    await dialog.getByLabel("Название токена", { exact: true }).fill(name);
    await dialog.getByRole("button", { name: "Создать личный токен", exact: true }).click();
    const secretInput = page.getByLabel("API-токен", { exact: true });
    await expect(secretInput).toBeVisible();
    const secret = await secretInput.inputValue();
    expect(secret).toMatch(/^SELF-[A-Za-z0-9]+$/);

    // A modal blocks tab clicks; browser history can leave while its result is open.
    await page.goBack();
    await expect(page.getByRole("tab", {
      name: origin === "personal" ? "Личные данные" : "Аккаунт", exact: true,
    })).toHaveAttribute("aria-selected", "true");
    await expect(dialog).toHaveCount(0);
    await expect(secretInput).toHaveCount(0);
    await page.goForward();
    await expect(page.getByRole("tab", { name: "API-токены", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("article", { name, exact: true })).toBeVisible();
    await expect(dialog).toHaveCount(0);
    await expect(secretInput).toHaveCount(0);
    await create.click();
    await expect(dialog.getByLabel("Название токена", { exact: true })).toHaveValue("");
    await expect(secretInput).toHaveCount(0);
    expect(await page.evaluate(value => JSON.stringify({
      local: { ...localStorage }, session: { ...sessionStorage },
    }).includes(value), secret)).toBe(false);
    expect(posts).toBe(1);
    // Leaving the one-time result hides the secret without revoking the credential.
    expect((await request.get(`${API}/auth/check`, {
      headers: { Authorization: `Bearer ${secret}` },
    })).status()).toBe(200);
  });
}

for (const outcome of ["success", "failure"] as const) {
  test(`personal save completes with ${outcome} while another profile section is open`, async ({ page, account }) => {
    const gate = deferred();
    let received = false;
    let attempts = 0;
    await page.route(`${API}/users/${account.id}`, async route => {
      if (route.request().method() !== "PATCH") return route.continue();
      attempts++;
      if (attempts > 1) return route.continue();
      const response = outcome === "success" ? await route.fetch() : null;
      if (response) expect(response.status()).toBe(200);
      received = true;
      await gate.pending;
      await route.fulfill(response ? { response } : { status: 503, headers: cors, json: {} });
    });
    try {
      await startSession(page, account);
      await page.goto("/profile");
      const form = page.locator(".profile-settings-form");
      const save = form.locator(".profile-form-actions > button");
      const status = form.locator('[role="status"]');
      const error = form.locator('[role="alert"]');
      const nickname = `${account.nickname}_sections`;
      await page.getByLabel("Имя", { exact: true }).fill("Имя после перехода");
      await page.getByLabel("Фамилия", { exact: true }).fill("Новая фамилия");
      await page.getByLabel("Ник", { exact: true }).fill(nickname);
      await save.click();
      await expect.poll(() => received).toBe(true);
      await expect(save).toBeDisabled();
      await leavePersonal(page);
      gate.release();
      // CSS locators still observe the mounted form while its panel is hidden.
      await expect(save).toBeEnabled();
      await expect(page.getByRole("tabpanel")).toHaveAccessibleName("API-токены");
      await expect(page.getByRole("alert")).toHaveCount(0);
      if (outcome === "success") {
        await expect(status).toHaveText("Изменения сохранены.");
        await expect(status).toBeHidden();
      } else {
        await expect(error).toHaveCount(1);
        await expect(error).toBeHidden();
      }
      await page.getByRole("tab", { name: "Личные данные", exact: true }).click();
      await expect(page.getByLabel("Имя", { exact: true })).toHaveValue("Имя после перехода");
      await expect(page.getByLabel("Фамилия", { exact: true })).toHaveValue("Новая фамилия");
      await expect(page.getByLabel("Ник", { exact: true })).toHaveValue(nickname);
      if (outcome === "failure") {
        await expect(error).toBeVisible();
        await expect(status).toHaveCount(0);
        const stored = await page.request.get(`${API}/users/me`);
        expect((await stored.json()).first_name).toBe(account.first_name);
        await save.click();
      }
      await expect(status).toHaveText("Изменения сохранены.");
      await expect(error).toHaveCount(0);
      expect(attempts).toBe(outcome === "success" ? 1 : 2);
      const stored = await page.request.get(`${API}/users/me`);
      expect(await stored.json()).toMatchObject({
        first_name: "Имя после перехода", last_name: "Новая фамилия", nickname,
      });
      await page.reload();
      await expect(page.getByLabel("Имя", { exact: true })).toHaveValue("Имя после перехода");
      await expect(page.getByLabel("Ник", { exact: true })).toHaveValue(nickname);
    } finally {
      gate.release();
    }
  });

  test(`avatar upload completes with ${outcome} outside personal details and retains the form draft`, async ({ page, account }) => {
    const gate = deferred();
    let received = false;
    let attempts = 0;
    await page.route(`${API}/users/me/avatar`, async route => {
      if (route.request().method() !== "PUT") return route.continue();
      attempts++;
      // Supply the bytes explicitly: WebKit cannot replay an intercepted File body.
      const response = outcome === "success" || attempts > 1
        ? await route.fetch({ postData: png }) : null;
      if (response) expect(response.status()).toBe(200);
      if (attempts === 1) {
        received = true;
        await gate.pending;
      }
      await route.fulfill(response ? { response } : { status: 503, headers: cors, json: {} });
    });
    try {
      await startSession(page, account);
      await page.goto("/profile");
      const editor = page.locator(".avatar-editor");
      const upload = editor.locator('input[type="file"]');
      const status = editor.locator('[role="status"]');
      const error = editor.locator('[role="alert"]');
      const nickname = `${account.nickname}_draft`;
      await page.getByLabel("Имя", { exact: true }).fill("Черновик имени");
      await page.getByLabel("Ник", { exact: true }).fill(nickname);
      await upload.setInputFiles(avatar);
      await expect.poll(() => received).toBe(true);
      await expect(upload).toBeDisabled();
      await expect(page.locator(".profile-form-actions > button")).toBeDisabled();
      await leavePersonal(page);
      gate.release();
      await expect(upload).toBeEnabled();
      await expect(page.getByRole("tabpanel")).toHaveAccessibleName("API-токены");
      await expect(page.getByRole("alert")).toHaveCount(0);
      if (outcome === "success") {
        await expect(status).toHaveText("Изменения сохранены.");
        await expect(status).toBeHidden();
      } else {
        await expect(error).toHaveCount(1);
        await expect(error).toBeHidden();
      }
      await page.getByRole("tab", { name: "Личные данные", exact: true }).click();
      await expect(page.getByLabel("Имя", { exact: true })).toHaveValue("Черновик имени");
      await expect(page.getByLabel("Ник", { exact: true })).toHaveValue(nickname);
      if (outcome === "failure") {
        await expect(error).toBeVisible();
        await expect(status).toHaveCount(0);
        await expect(editor.locator("img")).toHaveCount(0);
        const stored = await page.request.get(`${API}/users/me`);
        expect((await stored.json()).avatar_version).toBeNull();
        // Selecting the same file again must retry after the input was reset.
        await upload.setInputFiles(avatar);
      }
      await expect(status).toHaveText("Изменения сохранены.");
      await expect(error).toHaveCount(0);
      await expect(editor.locator("img")).toBeVisible();
      await expect.poll(() => editor.locator("img").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
      await expect(page.locator(".user-menu .avatar img")).toBeVisible();
      expect(attempts).toBe(outcome === "success" ? 1 : 2);
      const stored = await page.request.get(`${API}/users/me`);
      const user = await stored.json();
      expect(user.avatar_version).toBeTruthy();
      expect(user.first_name).toBe(account.first_name);
      expect(user.nickname).toBe(account.nickname);
      await expect(page.getByLabel("Имя", { exact: true })).toHaveValue("Черновик имени");
      await expect(page.getByLabel("Ник", { exact: true })).toHaveValue(nickname);
      await page.reload();
      await expect(editor.locator("img")).toBeVisible();
      await expect(page.getByLabel("Имя", { exact: true })).toHaveValue(account.first_name);
    } finally {
      gate.release();
    }
  });
}

for (const width of [320, 640, 641]) {
  for (const language of ["ru", "en"] as const) {
    test(`personal islands and validation remain usable at ${width}px in ${language}`, async ({ page, account }, info) => {
      const ru = language === "ru";
      await page.setViewportSize({ width, height: 720 });
      await page.addInitScript(language => localStorage.setItem("tmc-language", language), language);
      await startSession(page, account);
      await page.goto("/profile");
      const panel = page.getByRole("tabpanel", { name: ru ? "Личные данные" : "Personal details", exact: true });
      const sections = panel.locator(".profile-section");
      await expect(sections).toHaveCount(3);
      await page.evaluate(() => document.fonts.ready);
      async function fits() {
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        for (const section of await sections.all()) {
          const label = section.locator(".profile-section-label");
          const card = section.locator(":scope > .panel");
          await expect(label.getByRole("heading")).toBeVisible();
          const caption = (await label.boundingBox())!;
          const island = (await card.boundingBox())!;
          expect(island.x).toBeGreaterThanOrEqual(0);
          expect(island.x + island.width).toBeLessThanOrEqual(width);
          expect(await card.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
          if (width <= 640) expect(caption.y + caption.height).toBeLessThanOrEqual(island.y);
          else expect(caption.x + caption.width).toBeLessThan(island.x);
        }
      }
      await fits();
      const form = panel.locator(".profile-settings-form");
      const save = form.getByRole("button", { name: ru ? "Сохранить изменения" : "Save changes", exact: true });
      const error = form.getByRole("alert");
      let attempts = 0;
      await page.route(`${API}/users/${account.id}`, async route => {
        if (route.request().method() !== "PATCH") return route.continue();
        if (++attempts > 1) return route.continue();
        await route.fulfill({ status: 503, headers: cors, json: {} });
      });
      await page.getByLabel(ru ? "Имя" : "First name", { exact: true }).fill("Новое имя");
      await panel.locator('input[type="file"]').setInputFiles({
        name: "oversized.png", mimeType: "image/png", buffer: Buffer.alloc(2 * 1024 * 1024 + 1),
      });
      const avatarError = panel.locator(".avatar-editor").getByRole("alert");
      await avatarError.scrollIntoViewIfNeeded();
      // Native scrolling can round the bottom edge by a fraction of a CSS pixel.
      await expect(avatarError).toBeInViewport({ ratio: 0.99 });
      await save.scrollIntoViewIfNeeded();
      await expect(save).toBeInViewport({ ratio: 0.99 });
      await save.click();
      await expect(error).toBeVisible();
      await error.scrollIntoViewIfNeeded();
      await expect(error).toBeInViewport({ ratio: 0.99 });
      const errorBox = (await error.boundingBox())!;
      expect(errorBox.x).toBeGreaterThanOrEqual(0);
      expect(errorBox.x + errorBox.width).toBeLessThanOrEqual(width);
      expect(await error.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      await fits();
      await page.screenshot({ path: info.outputPath("personal-islands-errors.png"), fullPage: true });
      await save.scrollIntoViewIfNeeded();
      await expect(save).toBeInViewport({ ratio: 0.99 });
      await save.click();
      const status = form.getByRole("status");
      await expect(status).toHaveText(ru ? "Изменения сохранены." : "Changes saved.");
      await expect(error).toHaveCount(0);
      expect(attempts).toBe(2);
      await page.reload();
      await expect(page.getByLabel(ru ? "Имя" : "First name", { exact: true })).toHaveValue("Новое имя");
    });
  }
}
