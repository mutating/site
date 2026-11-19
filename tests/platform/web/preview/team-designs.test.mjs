import assert from "node:assert/strict";
import { test } from "node:test";
import { chromium, firefox, webkit } from "playwright";

const base = process.env.TEAM_PREVIEW_URL ?? "http://localhost:18784/team.html";
const engines = { chromium, firefox, webkit };

async function withPage(engine, size, run) {
  const browser = await engines[engine].launch();
  try {
    const page = await browser.newPage({ viewport: size });
    await run(page);
  } finally {
    await browser.close();
  }
}

const team = (page) => page.getByRole("region", { name: "Команда", exact: true });
const choice = (page) => page.getByRole("combobox", { name: "Вариант виджета" });

async function choose(page, number) {
  await choice(page).selectOption(String(number));
  await team(page).and(page.locator(`[data-variant="${number}"]`)).waitFor();
}

async function activeControlIsVisible(page, locator) {
  await page.waitForFunction((selector) => {
    const control = document.activeElement;
    if (!control?.matches(selector)) return false;
    const bounds = control.getBoundingClientRect();
    const toolbar = document.querySelector(".team-preview-toolbar");
    const top = toolbar.contains(control) ? 0 : toolbar.getBoundingClientRect().bottom;
    return bounds.height > 0 && bounds.top >= top - 1 && bounds.bottom <= innerHeight + 1;
  }, locator);
}

function brokenWords(locator) {
  return locator.evaluateAll((elements) => elements.flatMap((element) => {
    const node = element.firstChild;
    if (!(node instanceof Text) || !element.getClientRects().length) return [];
    return [...node.data.matchAll(/[\p{L}]+/gu)].flatMap(({ 0: word, index }) => {
      const range = document.createRange();
      range.setStart(node, index);
      range.setEnd(node, index + word.length);
      return range.getClientRects().length > 1 ? [word] : [];
    });
  }));
}

test("the comparison gallery opens twenty team designs in the same organization", async () => {
  await withPage("chromium", { width: 1440, height: 1000 }, async (page) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${base}#all`);
    const gallery = page.locator(".team-gallery-card");
    await gallery.last().waitFor();
    assert.equal(await gallery.count(), 20);
    await page.getByRole("button", { name: "Приглашения", exact: true }).click();
    const visibleInvites = page.locator(".team-gallery-card .team-invite-action:visible");
    await visibleInvites.nth(19).waitFor();
    assert.equal(await visibleInvites.count(), 20);
    await page.getByRole("button", { name: "Участники", exact: true }).click();
    await gallery.first().getByRole("link").click();
    await page.locator(".organization-hero").waitFor();
    const layout = page.locator(".organization-hero, .organization-overview, .organization-computers, .organization-history-grid");
    const context = await layout.allTextContents();
    assert.equal(context.length, 4);
    for (let number = 61; number <= 80; number++) {
      await choose(page, number);
      assert.deepEqual(await layout.allTextContents(), context);
      assert.equal(await team(page).count(), 1);
    }
    await page.getByRole("link", { name: "Все 20" }).click();
    await activeControlIsVisible(page, '.team-gallery a[href="#61"]');
    assert.deepEqual(errors, []);
  });
});

for (const engine of ["chromium", "firefox", "webkit"]) {
  test(`${engine} keeps the mobile labels readable and the whole page on-theme`, async () => {
    await withPage(engine, { width: 320, height: 568 }, async (page) => {
      await page.goto(`${base}#61`);
      for (const [number, selector] of [
        [63, ".team-widget [role=tab]"],
        [68, ".team-person-name strong, .team-person .team-role"],
        [71, ".team-person .team-role"],
        [72, ".team-person .team-role"],
      ]) {
        await choose(page, number);
        await page.evaluate(() => document.fonts.ready);
        assert.deepEqual(await brokenWords(page.locator(selector)), [], `Variant ${number}`);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollHeight - document.querySelector(".team-showcase").scrollHeight <= 1), true);
      await page.getByRole("link", { name: "Все 20" }).click();
      await page.locator(".team-gallery-card").last().waitFor();
      const labels = page.locator(".team-gallery-sample :is(.section-tabs button, .team-person-name strong, .team-role)");
      for (const width of [320, 1140, 1280, 1440]) {
        await page.setViewportSize({ width, height: 800 });
        for (const pane of ["Участники", "Приглашения"]) {
          const switcher = page.getByRole("button", { name: pane, exact: true });
          await switcher.click();
          await switcher.and(page.locator('[aria-pressed="true"]')).waitFor();
          assert.deepEqual(await brokenWords(labels), [], `${pane} at ${width}px`);
          if (pane === "Приглашения") {
            const outOfRow = await page.locator(".team-gallery-sample .team-invite > summary").evaluateAll((rows) => rows.flatMap((row) => {
              const bounds = row.getBoundingClientRect();
              const icon = row.querySelector(".team-disclosure-icon").getBoundingClientRect();
              return icon.top >= bounds.top - 1 && icon.bottom <= bounds.bottom + 1 &&
                icon.left >= bounds.left - 1 && icon.right <= bounds.right + 1 ? [] : [row.closest(".team-widget").dataset.variant];
            }));
            assert.deepEqual(outOfRow, [], `Invitation disclosure icons at ${width}px`);
          }
        }
      }
    });
  });
}

test("keyboard keeps focus and scroll position in the pinned design picker", async () => {
  await withPage("firefox", { width: 320, height: 568 }, async (page) => {
    await page.goto(`${base}#79`);
    await page.getByRole("button", { name: "К виджету" }).click();
    const next = page.locator(".team-design-picker a:last-child");
    await next.press("Enter");
    await team(page).and(page.locator('[data-variant="80"]')).waitFor();
    await activeControlIsVisible(page, ".team-design-picker a:last-child");
    await choice(page).evaluate((control) => control.focus({ preventScroll: true }));
    const scroll = await page.evaluate(() => window.scrollY);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    assert.ok(Math.abs(await page.evaluate(() => window.scrollY) - scroll) <= 1);
    await next.press("Enter");
    await team(page).and(page.locator('[data-variant="61"]')).waitFor();
    await activeControlIsVisible(page, ".team-design-picker a:last-child");
  });
});

test("keyboard reaches the panel from the bottom tabs and Back dismisses the demo dialog", async () => {
  await withPage("chromium", { width: 320, height: 568 }, async (page) => {
    await page.goto(`${base}#all`);
    await page.locator('.team-gallery-caption a[href="#78"]').click();
    await team(page).getByRole("tab", { name: /^Участники/ }).press("ArrowRight");
    await page.keyboard.press("Tab");
    await activeControlIsVisible(page, ".team-invite-action");
    await page.keyboard.press("Enter");
    await page.getByRole("dialog", { name: "Доступ к организации" }).waitFor();
    await page.goBack();
    await page.locator(".team-gallery-card").last().waitFor();
    assert.equal(await page.getByRole("dialog").count(), 0);
    await activeControlIsVisible(page, '.team-gallery a[href="#78"]');
  });
});

test("editing a role, revoking an invitation and resetting demo data leave visible focus", async () => {
  await withPage("chromium", { width: 320, height: 568 }, async (page) => {
    await page.goto(`${base}#69`);
    const danil = team(page).locator('summary[data-member="danil"]');
    await danil.press("Enter");
    await team(page).locator(".team-person[open]").getByRole("button", { name: "Изменить права" }).press("Enter");
    await page.getByRole("dialog").getByRole("combobox", { name: "Роль", exact: true }).selectOption("Администратор");
    await page.getByRole("dialog").getByRole("button", { name: "Сохранить", exact: true }).press("Enter");
    await activeControlIsVisible(page, 'summary[data-member="danil"]');
    await page.locator('[data-group="admin"] summary[data-member="danil"]').waitFor();
    await choose(page, 61);
    await team(page).getByRole("tab", { name: /^Приглашения/ }).click();
    await team(page).locator(".team-invite > summary").last().press("Enter");
    await team(page).getByRole("button", { name: "Отозвать приглашение" }).press("Enter");
    await page.getByRole("dialog").getByRole("button", { name: "Отозвать", exact: true }).press("Enter");
    await activeControlIsVisible(page, ".team-invite-action");
    assert.equal(await team(page).locator(".team-invite").count(), 2);
    await page.getByRole("button", { name: "Сбросить демоданные" }).press("Enter");
    await activeControlIsVisible(page, ".team-preview-toolbar :is(a, select)");
    assert.equal(await team(page).locator(".team-invite").count(), 3);
  });
});

test("success notice leaves the focused add-member button visible and clickable", async () => {
  await withPage("chromium", { width: 1440, height: 930 }, async (page) => {
    await page.goto(`${base}#77`);
    const invitations = team(page).locator(".team-section-disclosures > details").nth(1);
    await invitations.locator(":scope > summary").press("Enter");
    const add = team(page).getByRole("button", { name: "Добавить участника", exact: true });
    await add.press("Enter");
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("combobox", { name: "Способ предоставления доступа" }).selectOption("add");
    await dialog.getByRole("textbox", { name: "Почта или ник" }).fill("@preview_test");
    await dialog.getByRole("button", { name: "Добавить участника", exact: true }).press("Enter");
    await team(page).getByText("@preview_test", { exact: true }).waitFor();
    await page.getByRole("status").filter({ hasText: "Участник добавлен" }).waitFor();
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await add.evaluate((button) => {
      const notice = document.querySelector(".preview-notice")?.getBoundingClientRect();
      if (!notice || button !== document.activeElement) return false;
      const rect = button.getBoundingClientRect();
      const overlaps = rect.right > notice.left && rect.left < notice.right && rect.bottom > notice.top && rect.top < notice.bottom;
      return !overlaps && button.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
    }), true);
  });
});
