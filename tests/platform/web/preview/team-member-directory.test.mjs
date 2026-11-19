import assert from "node:assert/strict";
import { test } from "node:test";
import { chromium, devices, firefox, webkit } from "playwright";

const base = process.env.TEAM_PREVIEW_URL ?? "http://localhost:18784/team.html";
const engines = { chromium, firefox, webkit };

async function withPage(engine, viewport, run) {
  const browser = await engines[engine].launch();
  try {
    const page = await browser.newPage({ ...(engine === "webkit" && viewport.width < 500 ? devices["iPhone SE"] : {}), viewport });
    await run(page);
  } finally {
    await browser.close();
  }
}

const gallery = (page) => page.locator(".team-link-gallery .team-gallery-card");
const widget = (page) => page.locator("#team-widget");
const links = (page) => widget(page).locator("[data-directory-link]");
const directory = (page) => page.getByRole("region", { name: "Полный список участников" });
const size = (page) => page.getByRole("combobox", { name: "Размер демокоманды" });
const variant = (page) => page.getByRole("combobox", { name: "Вариант виджета" });
const search = (region) => region.getByRole("searchbox", { name: "Найти участника" });

test("all ten variants show five rows and open the same complete directory", async () => {
  await withPage("chromium", { width: 1440, height: 1100 }, async (page) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${base}#all`);
    await page.getByRole("link", { name: "10 способов открыть список участников" }).click();
    await gallery(page).last().waitFor();
    assert.equal(await gallery(page).count(), 10);
    assert.equal(await gallery(page).locator("[data-directory-link]").count(), 10);
    for (let number = 81; number <= 90; number++) {
      const card = gallery(page).locator(`.team-gallery-caption a[href="#${number}"]`);
      await card.click();
      await widget(page).locator(`[data-variant="${number}"]`).waitFor();
      assert.equal(await widget(page).locator(".team-person").count(), 5);
      assert.equal(await links(page).count(), 1);
      await links(page).click();
      await directory(page).waitFor();
      assert.equal(await directory(page).locator(".team-person").count(), 20);
      assert.match(await directory(page).getByRole("status").textContent(), /1–20 из 2\s*000/);
      assert.ok(page.url().endsWith(`#members-${number}`));
      await page.goBack();
      await links(page).waitFor();
      assert.equal(await links(page).evaluate((link) => link === document.activeElement), true);
      await page.getByRole("link", { name: "Все 10" }).click();
      await card.waitFor();
      assert.equal(await card.evaluate((link) => link === document.activeElement), true);
    }
    assert.deepEqual(errors, []);
  });
});

for (const engine of ["chromium", "firefox", "webkit"]) {
  test(`${engine} thresholds, both themes and narrow layouts`, async () => {
    await withPage(engine, { width: 320, height: 620 }, async (page) => {
      await page.goto(`${base}#links`);
      await gallery(page).last().waitFor();
      for (const total of [4, 5, 6, 2000]) {
        await size(page).selectOption(String(total));
        await gallery(page).last().locator(".team-person").nth(Math.min(total, 5) - 1).waitFor();
        assert.equal(await gallery(page).locator(".team-person").count(), Math.min(total, 5) * 10);
        assert.equal(await gallery(page).locator("[data-directory-link]").count(), total > 5 ? 10 : 0);
      }
      await page.getByRole("button", { name: "Тёмная тема" }).click();
      await gallery(page).first().locator(".team-gallery-caption a").click();
      for (let number = 81; number <= 90; number++) {
        await variant(page).selectOption(String(number));
        await widget(page).locator(`[data-variant="${number}"]`).waitFor();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
        assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
        assert.equal(await links(page).isVisible(), true);
      }
      await size(page).selectOption("5");
      assert.equal(await links(page).count(), 0);
      await size(page).selectOption("6");
      await links(page).click();
      await directory(page).waitFor();
      assert.equal(await directory(page).locator(".team-person").count(), 6);
      assert.equal(await page.getByRole("navigation", { name: "Страницы списка участников" }).count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    });
  });
}

test("search covers all 2,000 members, opens the matching directory and keeps the widget search on return", async () => {
  await withPage("chromium", { width: 390, height: 770 }, async (page) => {
    await page.goto(`${base}#83`);
    await search(widget(page)).fill("@andreev_2000");
    await widget(page).locator('summary[data-member="directory-2000"]').waitFor();
    assert.equal(await widget(page).locator(".team-person").count(), 1);
    await links(page).click();
    await directory(page).locator('summary[data-member="directory-2000"]').waitFor();
    assert.equal(await search(directory(page)).inputValue(), "@andreev_2000");
    assert.equal(await directory(page).locator(".team-person").count(), 1);
    await search(directory(page)).fill("");
    await directory(page).locator(".team-person").nth(19).waitFor();
    await page.getByRole("combobox", { name: "Страница списка" }).selectOption("100");
    await directory(page).locator('summary[data-member="directory-2000"]').waitFor();
    assert.equal(await directory(page).locator(".team-person").last().locator("summary").getAttribute("data-member"), "directory-2000");
    assert.match(await directory(page).getByRole("status").textContent(), /1\s*981–2\s*000 из 2\s*000/);
    assert.equal(await directory(page).getByRole("button", { name: /Далее/ }).isDisabled(), true);
    await search(directory(page)).fill("несуществующий_ник_2001");
    await directory(page).getByText("Участники не найдены", { exact: true }).waitFor();
    assert.equal(await directory(page).locator(".team-person").count(), 0);
    await page.getByRole("link", { name: "К организации" }).click();
    await links(page).waitFor();
    assert.equal(await search(widget(page)).inputValue(), "@andreev_2000");
    assert.equal(await links(page).evaluate((link) => link === document.activeElement), true);
  });
});

test("adding the sixth member reveals the link while the preview stays at five", async () => {
  await withPage("chromium", { width: 1440, height: 1024 }, async (page) => {
    await page.goto(`${base}#85`);
    await size(page).selectOption("5");
    assert.equal(await links(page).count(), 0);
    await widget(page).getByRole("tab", { name: /^Приглашения/ }).click();
    await widget(page).getByRole("button", { name: "Добавить участника", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("combobox", { name: "Способ предоставления доступа" }).selectOption("add");
    await dialog.getByRole("textbox", { name: "Почта или ник" }).fill("sixth_member");
    await dialog.getByRole("button", { name: "Добавить участника", exact: true }).click();
    await widget(page).getByRole("tab", { name: /^Участники 6/ }).click();
    await links(page).waitFor();
    assert.equal(await widget(page).locator(".team-person").count(), 5);
    await links(page).click();
    await directory(page).getByText("@sixth_member", { exact: true }).waitFor();
    assert.equal(await directory(page).locator(".team-person").count(), 6);
  });
});

test("history restores the directory page and the visible organization link after visiting the gallery", async () => {
  await withPage("firefox", { width: 320, height: 568 }, async (page) => {
    await page.goto(`${base}#85`);
    await links(page).click();
    await directory(page).waitFor();
    await page.getByRole("combobox", { name: "Страница списка" }).selectOption("100");
    await directory(page).locator('summary[data-member="directory-2000"]').waitFor();
    await page.getByRole("link", { name: "Все 10" }).click();
    await gallery(page).last().waitFor();
    await page.goBack();
    await directory(page).locator('summary[data-member="directory-2000"]').waitFor();
    assert.equal(await page.getByRole("combobox", { name: "Страница списка" }).inputValue(), "100");
    await page.goBack();
    await links(page).waitFor();
    assert.equal(await links(page).evaluate((link) => {
      const { top, bottom } = link.getBoundingClientRect();
      return link === document.activeElement && top >= document.querySelector(".team-preview-toolbar").getBoundingClientRect().bottom - 1 && bottom <= innerHeight + 1;
    }), true);
  });
});

test("Forward into a previous directory keeps the newer widget query on Back", async () => {
  await withPage("chromium", { width: 390, height: 770 }, async (page) => {
    await page.goto(`${base}#83`);
    await search(widget(page)).fill("@andreev_2000");
    await links(page).click();
    await directory(page).waitFor();
    assert.equal(await search(directory(page)).inputValue(), "@andreev_2000");
    await page.goBack();
    await search(widget(page)).fill("Никитина");
    await page.goForward();
    await directory(page).waitFor();
    assert.equal(await search(directory(page)).inputValue(), "@andreev_2000");
    await page.goBack();
    await links(page).waitFor();
    assert.equal(await search(widget(page)).inputValue(), "Никитина");
  });
});

test("the adjacent list action preserves tab focus after ArrowRight", async () => {
  await withPage("chromium", { width: 320, height: 568 }, async (page) => {
    await page.goto(`${base}#82`);
    await widget(page).getByRole("tab", { name: /^Участники/ }).press("ArrowRight");
    const invitations = widget(page).getByRole("tab", { name: /^Приглашения/ });
    assert.equal(await invitations.getAttribute("aria-selected"), "true");
    assert.equal(await invitations.evaluate((tab) => tab === document.activeElement), true);
    await page.keyboard.press("Tab");
    assert.equal(await widget(page).getByRole("button", { name: "Добавить участника", exact: true }).evaluate((button) => button === document.activeElement), true);
  });
});

for (const engine of ["firefox", "webkit"]) {
  test(`${engine} keeps keyboard focus between the header and the bottom of the screen`, async () => {
    await withPage(engine, { width: 320, height: 568 }, async (page) => {
      const tab = engine === "webkit" ? "Alt+Tab" : "Tab";
      const back = engine === "webkit" ? "Alt+Shift+Tab" : "Shift+Tab";
      async function walk(scope, direction, steps) {
        for (let step = 0; step < steps; step++) {
          await page.keyboard.press(direction);
          await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          const problem = await page.evaluate((selector) => {
            const control = document.activeElement;
            if (!control?.closest(selector)) return null;
            const { top, bottom } = control.getBoundingClientRect();
            const toolbar = document.querySelector(".team-preview-toolbar").getBoundingClientRect();
            return top < toolbar.bottom - 1 || bottom > innerHeight + 1 ? { label: control.textContent, top, bottom } : null;
          }, scope);
          assert.equal(problem, null, `${direction} at step ${step + 1}: ${JSON.stringify(problem)}`);
        }
      }
      await page.goto(`${base}#81`);
      await page.getByRole("button", { name: "К виджету" }).click();
      await walk("#team-widget", tab, 8);
      await walk("#team-widget", back, 8);
      await page.goto(`${base}#members-90`);
      await directory(page).waitFor();
      await walk(".member-directory-page", tab, 24);
      await walk(".member-directory-page", back, 24);
    });
  });
}
