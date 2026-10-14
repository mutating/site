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
  await page.goto("power-cells");
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
  await page.getByRole("button", { name: "Выбрать язык" }).click();
  await expect(
    page
      .getByRole("row")
      .filter({ hasText: "Рабочая станция" })
      .locator("td.numeric"),
  ).toHaveText(["6.75 / 8", "6 / 8 GiB"]);
  await page.goto("power-cells");
  await metrics(
    page.getByRole("region", { name: "Personal computers" }),
    ["7.25", "7.5"],
    ["8.5", "9.5"],
    "2",
  );
});

test("seven-day history has exact values, independent CPU/RAM scales and four distinct step lines", async ({
  page,
  request,
  account,
}) => {
  const computer = await addComputer(request, account, "История");
  await seedHistory(request, computer.id);
  await page.clock.setFixedTime(historyNow);
  await login(page, account);
  await page.goto("power-cells");
  const pool = page.getByRole("region", { name: "Личные компьютеры" });
  const svg = pool.locator("svg.capacity-chart");
  await expect(svg.locator("path[data-series]")).toHaveCount(4);
  await pool.locator("summary").click();
  const rows = pool.locator("tbody tr");
  await expect(rows).toHaveCount(4);
  const expected = [
    ["2,5", "0,5", "4", "1,5"],
    ["6", "1,5", "12", "4"],
    ["0,5", "0,25", "2", "0,75"],
    ["0,5", "0,25", "2", "0,75"],
  ];
  for (let i = 0; i < expected.length; i++) {
    await expect(rows.nth(i).locator("td:not(:first-child)")).toHaveText(
      expected[i],
    );
    await expect(rows.nth(i).locator("td").first()).toContainText(
      ["01.01.2026", "03.01.2026", "05.01.2026", "08.01.2026"][i],
    );
  }
  // Independent pixel oracle: CPU axis 0..10 cores, RAM 0..20 GiB.
  const viewBox = (await svg.getAttribute("viewBox"))!.split(" ").map(Number);
  const width = viewBox[2] - 100,
    height = viewBox[3] - 90;
  const x = (fraction: number) => (width * fraction).toFixed(2);
  const y = (fraction: number) => (height * fraction).toFixed(2);
  for (const [field, levels] of Object.entries({
    cpu_millis: [0.75, 0.4, 0.95],
    cpu_occupied_millis: [0.95, 0.85, 0.975],
    memory_bytes: [0.8, 0.4, 0.9],
    memory_occupied_bytes: [0.925, 0.8, 0.9625],
  })) {
    await expect(svg.locator(`[data-series="${field}"]`)).toHaveAttribute(
      "d",
      `M0.00,${y(levels[0])}H${x(2 / 7)}V${y(levels[1])}H${x(4 / 7)}V${y(levels[2])}H${x(1)}V${y(levels[2])}`,
    );
  }
  await expect(
    svg.locator('[data-series="cpu_occupied_millis"]'),
  ).toHaveAttribute("stroke-dasharray", "6 5");
  expect(
    await svg.locator('[data-series="cpu_millis"]').getAttribute("stroke"),
  ).not.toBe(
    await svg.locator('[data-series="memory_bytes"]').getAttribute("stroke"),
  );
  const toggle = pool.getByRole("button", {
    name: "CPU · занято",
    exact: true,
  });
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(svg.locator('[data-series="cpu_occupied_millis"]')).toHaveCount(
    0,
  );
  await toggle.click();
  await expect(svg.locator('[data-series="cpu_occupied_millis"]')).toHaveCount(
    1,
  );
});

test("empty capacity stays zero and the unknown part of history is disclosed", async ({
  page,
  account,
}) => {
  await page.clock.setFixedTime(new Date("2026-01-04T00:00:00Z"));
  await login(page, account);
  await page.goto("power-cells");
  const pool = page.getByRole("region", { name: "Личные компьютеры" });
  await metrics(pool, ["0", "0"], ["0", "0"], "0");
  await expect(pool.locator(".meter > span").first()).toHaveAttribute(
    "style",
    "width: 0%;",
  );
  await expect(pool.locator(".chart-note").last()).toContainText("1 янв.");
  const path = await pool
    .locator('[data-series="cpu_millis"]')
    .getAttribute("d");
  expect(path).not.toMatch(/^M0\.00,/); // No line in the unrecorded portion.
});
