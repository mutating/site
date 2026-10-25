import { API, expect, headers, startSession, test } from "./fixtures";
import type { Locator, Page } from "@playwright/test";

const screens = [
  [320, 568],
  [390, 844],
  [568, 320],
  [600, 960],
  [768, 1024],
  [820, 1180],
  [1024, 768],
  [1101, 768],
  [1180, 820],
  [1366, 1024],
] as const;

async function fits(page: Page, dialog?: Locator) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  if (dialog) {
    const box = (await dialog.boundingBox())!;
    const viewport = page.viewportSize()!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 1);
    expect(
      await dialog.evaluate(
        (element) => element.scrollWidth <= element.clientWidth + 1,
      ),
    ).toBe(true);
  }
}

/**
 * Phone and tablet breakpoints must keep long owner names, native selectors and
 * token actions inside the screen, with every step reachable in either orientation.
 * The revoke warning used to leave long organization names unbroken, clipping
 * the owner and forcing horizontal scrolling inside the confirmation dialog.
 * Long selector options also squeezed the toolbar's create button onto two lines
 * on tablets even when its label could fit beside the filter on a single line.
 */
for (const [width, height] of screens) {
  for (const language of ["ru", "en"] as const) {
    test(`${width}x${height} ${language}: responsive registry, issuance and revoke`, async ({
      page,
      request,
      account,
      isMobile,
    }, info) => {
      const ru = language === "ru";
      const activate = (control: Locator) =>
        isMobile ? control.tap() : control.click();
      await page.setViewportSize({ width, height });
      await page.addInitScript(
        ({ language }) => {
          localStorage.setItem("tmc-language", language);
          localStorage.setItem(
            "tmc-theme",
            language === "ru" ? "dark" : "light",
          );
          Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: { writeText: async () => {} },
          });
        },
        { language },
      );
      const companyName =
        "Лаборатория распределённых вычислений — " + "Компания".repeat(10);
      const companies = [];
      for (const name of [companyName, "Студия"]) {
        const response = await request.post(`${API}/companies`, {
          headers: headers(account),
          data: { name },
        });
        expect(response.status()).toBe(201);
        companies.push(await response.json());
      }
      const tokenName = "Ночные сборки — " + "ДлинноеНазвание".repeat(5);
      for (const owner of [
        { user_id: account.id },
        { company_id: companies[0].id },
      ]) {
        expect(
          (
            await request.post(`${API}/api-tokens`, {
              headers: headers(account),
              data: { ...owner, name: tokenName, scopes: ["read", "execute"] },
            })
          ).status(),
        ).toBe(201);
      }
      await startSession(page, account);
      // Match the real secret's length and format without saving a working credential.
      await page.route(`${API}/api-tokens`, async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        const response = await route.fetch();
        await route.fulfill({
          response,
          json: {
            ...(await response.json()),
            token: `ORG-${"0123456789abcdef".repeat(4)}`,
          },
        });
      });
      await page.goto("api-tokens");
      await expect(page.locator(".token-registry-row")).toHaveCount(2);
      await page.evaluate(() => document.fonts.ready);
      const trigger = page.getByRole("button", {
        name: ru ? "Создать токен" : "Create token",
        exact: true,
      });
      expect((await trigger.boundingBox())!.height).toBeLessThanOrEqual(50);
      const capture = async (name: string) => {
        if (info.project.name === "desktop")
          await page.screenshot({
            path: info.outputPath(`${name}.png`),
            fullPage: !(await page.getByRole("dialog").count()),
          });
      };
      await fits(page);
      await capture("registry");
      await page
        .getByLabel(ru ? "Владелец токенов" : "Token owner", { exact: true })
        .selectOption("personal");
      await expect(page.locator(".token-registry-row")).toHaveCount(1);
      await activate(trigger);
      const dialog = page.getByRole("dialog");
      await fits(page, dialog);
      await dialog.evaluate((element) => (element.scrollTop = 0));
      await capture("personal");
      await dialog
        .getByRole("radio", { name: ru ? /Организация/ : /Organization/ })
        .check();
      await dialog
        .getByRole("combobox", {
          name: ru ? "Организация-владелец" : "Owning organization",
          exact: true,
        })
        .selectOption(companies[0].id);
      await fits(page, dialog);
      await dialog.evaluate((element) => (element.scrollTop = 0));
      await capture("company");
      await dialog
        .getByLabel(ru ? "Название токена" : "Token name", { exact: true })
        .fill("Responsive token");
      await dialog.locator("summary").click();
      await fits(page, dialog);
      const create = dialog.getByRole("button", {
        name: ru ? "Создать токен организации" : "Create organization token",
        exact: true,
      });
      await create.scrollIntoViewIfNeeded();
      // Native dialog scrolling can leave a fraction of a CSS pixel at its edge.
      await expect(create).toBeInViewport({ ratio: 0.99 });
      await capture("submit");
      await activate(create);
      await expect(dialog.locator(".issued-token")).toBeVisible();
      await fits(page, dialog);
      await capture("issued");
      await activate(
        dialog.getByRole("button", {
          name: ru ? "Скопировать токен" : "Copy token",
          exact: true,
        }),
      );
      await expect(dialog.getByRole("status")).not.toBeEmpty();
      await activate(
        dialog.getByRole("button", {
          name: ru ? "Готово" : "Done",
          exact: true,
        }),
      );
      await page
        .getByLabel(ru ? "Владелец токенов" : "Token owner", { exact: true })
        .selectOption(companies[0].id);
      const row = page.getByRole("article", {
        name: "Responsive token",
        exact: true,
      });
      await activate(
        row.getByRole("button", {
          name: ru ? "Отозвать токен" : "Revoke token",
          exact: true,
        }),
      );
      await fits(page, dialog);
      await capture("revoke");
      await dialog.getByRole("textbox").fill("Responsive token");
      await activate(
        dialog.getByRole("button", {
          name: ru ? "Подтвердить" : "Confirm",
          exact: true,
        }),
      );
      await expect(row.locator(".token-status")).toHaveText(
        ru ? "Отозван" : "Revoked",
      );
      await fits(page);
    });
  }
}

/**
 * The profile's token entry used to place its text and link directly on the
 * rounded panel border because the shared panel has no default padding.
 * Both must remain comfortably inside the card at phone, tablet and desktop
 * widths, and the link must still open the token registry in either language.
 */
for (const width of [320, 768, 1440]) {
  for (const language of ["ru", "en"] as const) {
    test(`profile token entry has readable insets at ${width}px ${language}`, async ({ page, account }, info) => {
      const ru = language === "ru";
      await page.setViewportSize({ width, height: 1000 });
      await page.addInitScript(language => localStorage.setItem("tmc-language", language), language);
      await startSession(page, account);
      await page.goto("/profile");
      const entry = page.locator(".token-profile-link");
      const link = entry.getByRole("link", { name: /API/ });
      await expect(entry).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await entry.scrollIntoViewIfNeeded();
      const card = (await entry.boundingBox())!;
      for (const content of [entry.locator("div"), link]) {
        const box = (await content.boundingBox())!;
        expect(box.x - card.x).toBeGreaterThanOrEqual(16);
        expect(box.y - card.y).toBeGreaterThanOrEqual(16);
        expect(card.x + card.width - box.x - box.width).toBeGreaterThanOrEqual(16);
        expect(card.y + card.height - box.y - box.height).toBeGreaterThanOrEqual(16);
      }
      await entry.screenshot({ path: info.outputPath("profile-token-entry.png") });
      await link.click();
      await expect(page).toHaveURL(/\/api-tokens$/);
      await expect(page.getByRole("button", { name: ru ? "Создать токен" : "Create token", exact: true })).toBeEnabled();
    });
  }
}
