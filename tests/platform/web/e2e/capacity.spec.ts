import { selectLanguage } from "./header-controls";
import type { Locator } from "@playwright/test";
import {
  API,
  addComputer,
  expect,
  headers,
  login,
  makeAccount,
  test,
} from "./fixtures";
import { GiB, historyNow, reserve, seedHistory } from "./resources";

test.use({ timezoneId: "UTC" });

async function metrics(
  pool: Locator,
  free: string[],
  total: string[],
  count: string,
) {
  await expect(pool.locator(".metric-free")).toHaveText(free);
  await expect(pool.locator(".metric-total")).toHaveText(
    total.map((x) => `/ ${x}`),
  );
  await expect(pool.getByTestId("computer-count")).toHaveText(count);
}

test("allocated limits minus actual reservations are correct in both pools and computer rows", async ({
  page,
  request,
  account,
}) => {
  await addComputer(request, account, "Рабочая станция");
  await addComputer(request, account, "Малый узел", undefined, true, {
    cpu_millis: 500,
    memory_bytes: 1.5 * GiB,
  });
  await addComputer(request, account, "Отключён", undefined, false);
  const disabled = await addComputer(
    request,
    account,
    "Выключен пользователем",
  );
  expect(
    (
      await request.patch(`${API}/computers/${disabled.id}`, {
        headers: headers(account),
        data: { enabled: false },
      })
    ).status(),
  ).toBe(200);
  const other = await makeAccount(request, "Другой владелец");
  await addComputer(request, other, "Чужой компьютер");
  const companyResponse = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Лаборатория" },
  });
  expect(companyResponse.status()).toBe(201);
  const company = await companyResponse.json();
  await addComputer(request, account, "Сервер команды", company.id, true, {
    cpu_millis: 4000,
    memory_bytes: 12 * GiB,
  });
  await reserve(request, account, 1250, 2 * GiB);
  await reserve(request, account, 500, 3.25 * GiB, company.id);
  await login(page, account);
  // The home page must present the same organization's available/idle capacity.
  await metrics(
    page.getByRole("region", { name: company.name }),
    ["3,5", "8,75"],
    ["4", "12"],
    "1",
  );
  await page.goto("compute");
  const personal = page.getByRole("region", { name: "Личные компьютеры" });
  await metrics(personal, ["7,25", "7,5"], ["8,5", "9,5"], "2");
  await metrics(
    page.getByRole("region", { name: company.name }),
    ["3,5", "8,75"],
    ["4", "12"],
    "1",
  );
  const fills = await personal
    .locator(".meter > span")
    .evaluateAll((elements) =>
      elements.map((e) => parseFloat((e as HTMLElement).style.width)),
    );
  expect(fills[0]).toBeCloseTo(85.2941, 3);
  expect(fills[1]).toBeCloseTo(78.9474, 3);
  await personal.getByRole("link").click();
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await expect(
    page
      .getByRole("row")
      .filter({ hasText: "Рабочая станция" })
      .locator("td.numeric"),
  ).toHaveText(["6,75 / 8", "6 / 8 GiB"]);
  await expect(
    page
      .getByRole("row")
      .filter({ hasText: "Малый узел" })
      .locator("td.numeric"),
  ).toHaveText(["0,5 / 0,5", "1,5 / 1,5 GiB"]);
  await selectLanguage(page);
  await expect(
    page
      .getByRole("row")
      .filter({ hasText: "Рабочая станция" })
      .locator("td.numeric"),
  ).toHaveText(["6.75 / 8", "6 / 8 GiB"]);
  await page.goto("compute");
  await metrics(
    page.getByRole("region", { name: "Personal computers" }),
    ["7.25", "7.5"],
    ["8.5", "9.5"],
    "2",
  );
});

test("one plot overlays both resources with separate scales and a shared reading", async ({
  page,
  request,
  account,
  hasTouch,
}) => {
  const computer = await addComputer(request, account, "История");
  await seedHistory(request, computer.id);
  await page.clock.setFixedTime(historyNow);
  await login(page, account);
  await page.goto("compute");
  const pool = page.getByRole("region", { name: "Личные компьютеры" });
  const plot = pool.getByRole("slider", { name: "Мощности и загрузка" });
  const svg = plot.locator("svg.capacity-chart");
  await expect(pool.locator(".chart-period")).toHaveText(/1[–—]8 янв\./);
  await expect(pool.locator("svg.capacity-chart")).toHaveCount(1);
  await expect(svg.locator("path[data-series]")).toHaveCount(4);
  await expect(pool.locator("summary")).toHaveCount(0);
  await expect(svg.locator("path[data-fill]")).toHaveCount(2);
  await expect(svg.locator('[data-series="cpu_millis"]')).toHaveAttribute("d", /Q.*V.*Q/);
  await expect(svg.locator('[data-axis="cpu"] text')).toHaveText(["0", "1,5", "3", "4,5", "6"]);
  await expect(svg.locator('[data-axis="memory"] text')).toHaveText(["0", "3", "6", "9", "12"]);

  const readings = [
    ["2,5", "0,5", "4", "1,5"],
    ["6", "1,5", "12", "4"],
    ["0,5", "0,25", "2", "0,75"],
  ];
  await plot.focus();
  await plot.press("Home");
  await plot.dispatchEvent("pointerleave", { pointerType: "mouse" });
  await plot.dispatchEvent("pointermove", { pointerType: "mouse", clientX: 900, clientY: 250 });
  for (const [i, [cpu, busyCpu, ram, busyRam]] of readings.entries()) {
    await expect(plot).toHaveAttribute(
      "aria-valuetext",
      new RegExp(`^${[1, 3, 5][i]} янв.*CPU · мощность ${cpu} ядер · CPU · занято ${busyCpu} ядер · Память · мощность ${ram} GiB · Память · занято ${busyRam} GiB$`),
    );
    await expect(plot.locator(".chart-tooltip")).toBeVisible();
    await expect(svg.locator(".chart-cursor")).toHaveCount(1);
    await plot.press("ArrowRight");
  }
  await expect(plot).toHaveAttribute("aria-valuetext", /^8 янв/);
  await plot.press("Escape");
  await expect(plot.locator(".chart-tooltip")).toHaveCount(0);

  const toggle = pool.getByRole("button", { name: "CPU · занято", exact: true });
  const cpuReserved = pool.locator('[data-series="cpu_occupied_millis"]');
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(cpuReserved).toHaveCount(0);
  await toggle.click();
  await expect(cpuReserved).toHaveCount(1);

  const capacity = pool.getByRole("button", { name: "CPU · мощность", exact: true });
  await capacity.click();
  await expect(svg.locator('[data-fill="cpu"]')).toHaveCount(0);
  await expect(svg.locator('[data-fill="memory"]')).toHaveCount(1);
  if (hasTouch) await plot.tap();
  else await plot.hover();
  const tooltip = plot.locator(".chart-tooltip");
  await expect(tooltip.locator('[data-reading="cpu_occupied_millis"]')).toBeVisible();
  await expect(tooltip.locator('[data-reading="memory_bytes"]')).toBeVisible();
  await expect(tooltip.locator('[data-reading="cpu_millis"]')).toHaveCount(0);
  await expect(plot).not.toHaveAttribute("aria-valuetext", /CPU · мощность/);
  if (hasTouch) await pool.getByRole("heading", { name: "Личные компьютеры" }).tap();
  else await page.keyboard.press("Escape");
  await expect(tooltip).not.toBeVisible();

  await toggle.click();
  await expect(plot).toBeVisible();
  await expect(svg.locator("path[data-series]")).toHaveCount(2);
  await pool.getByRole("button", { name: "Память · мощность", exact: true }).click();
  await pool.getByRole("button", { name: "Память · занято", exact: true }).click();
  await expect(pool.getByText("Все линии скрыты")).toBeVisible();
  await expect(plot).toHaveCount(0);
  await capacity.click();
  await expect(plot).toBeVisible();
});

test("a short organization session fills the plot after a long idle period", async ({
  page,
  request,
  account,
}) => {
  const response = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Лаборатория" },
  });
  expect(response.status()).toBe(201);
  const company = await response.json();
  const computer = await addComputer(request, account, "Узел", company.id);
  await seedHistory(request, computer.id, "sparse");
  await page.clock.setFixedTime(historyNow);
  await login(page, account);
  await page.goto("compute");

  const pool = page.getByRole("region", { name: company.name });
  await expect(pool.locator(".chart-period")).toHaveText(/6 янв.*13:04.*13:59/);
  const plot = pool.getByRole("slider", { name: "Мощности и загрузка" });
  await expect(plot.locator('[data-axis="cpu"] text').last()).toHaveText("8");
  await plot.focus();
  await plot.press("Home");
  await plot.press("ArrowRight");
  await expect(plot).toHaveAttribute("aria-valuetext", /13:10:00.*CPU · мощность 5 ядер · CPU · занято 0 ядер/);
  await plot.press("ArrowRight");
  await expect(plot).toHaveAttribute("aria-valuetext", /13:22:00.*CPU · мощность 8 ядер · CPU · занято 3,5 ядер/);

  const capacity = pool.getByRole("button", { name: "CPU · мощность", exact: true });
  await capacity.click();
  const updated = await request.patch(`${API}/computers/${computer.id}`, {
    headers: headers(account),
    data: { enabled: false },
  });
  expect(updated.status()).toBe(200);
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(pool.getByTestId("computer-count")).toHaveText("0");
  await expect(capacity).toHaveAttribute("aria-pressed", "false");
  await expect(pool.locator('[data-series="cpu_millis"]')).toHaveCount(0);
});

test("backward tabbing reveals the plot below the sticky header on a narrow phone", async ({
  page,
  request,
  account,
}) => {
  await page.setViewportSize({ width: 320, height: 568 });
  const extraPool = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Другой пул" },
  });
  expect(extraPool.status()).toBe(201);
  const computer = await addComputer(request, account, "История");
  await seedHistory(request, computer.id);
  await page.clock.setFixedTime(historyNow);
  await login(page, account);
  await page.goto("compute");

  const pool = page.getByRole("region", { name: "Личные компьютеры" });
  const header = page.getByRole("banner");
  const plot = pool.getByRole("slider", { name: "Мощности и загрузка" });
  await expect(header).toHaveCSS("position", "sticky");
  const nextPool = page.getByRole("region", { name: "Другой пул" });
  await nextPool.getByRole("link").evaluate((link) => link.focus({ preventScroll: true }));
  await plot.evaluate((element) => {
    // Move the plot under the header while focus is on the following control.
    window.scrollBy(0, element.getBoundingClientRect().top - 10);
  });
  expect((await plot.boundingBox())!.y).toBeLessThan((await header.boundingBox())!.height);
  await page.keyboard.press("Shift+Tab");
  await expect(plot).toBeFocused();
  await expect.poll(async () => {
    const [chart, bar] = await Promise.all([plot.boundingBox(), header.boundingBox()]);
    return chart!.y - bar!.y - bar!.height;
  }).toBeGreaterThanOrEqual(0);
});

test("empty capacity stays zero and the unknown part of history is disclosed", async ({
  page,
  account,
}) => {
  await page.clock.setFixedTime(new Date("2026-01-04T00:00:00Z"));
  await login(page, account);
  await page.goto("compute");
  const pool = page.getByRole("region", { name: "Личные компьютеры" });
  await metrics(pool, ["0", "0"], ["0", "0"], "0");
  await expect(pool.locator(".meter > span").first()).toHaveAttribute(
    "style",
    "width: 0%;",
  );
  await expect(pool.locator(".chart-note")).toContainText("1 янв.");
  await expect(pool.getByText("За этот период не было доступных мощностей.")).toBeVisible();
  await expect(pool.getByRole("slider")).toHaveCount(0);
});
