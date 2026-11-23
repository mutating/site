import type { APIRequestContext, Locator, Page } from "@playwright/test";
import { addComputer, API, expect, headers, makeAccount, startSession, test, type Account } from "./fixtures";
import { switchOrganizationPeople } from "./organization-controls";

async function createOrganization(request: APIRequestContext, account: Account) {
  const result = await request.post(`${API}/companies`, {
    headers: headers(account), data: { name: "Северная лаборатория" },
  });
  expect(result.status()).toBe(201);
  return (await result.json()).id as string;
}

function widgets(page: Page) {
  return {
    computers: page.getByRole("region", { name: "Компьютеры", exact: true }),
    cpu: page.getByRole("region", { name: "История CPU", exact: true }),
    memory: page.getByRole("region", { name: "История памяти", exact: true }),
    team: page.getByRole("region", { name: "Команда", exact: true }),
  };
}

async function bounds(widget: Locator) {
  const box = await widget.boundingBox();
  expect(box).not.toBeNull();
  return { ...box!, bottom: box!.y + box!.height, right: box!.x + box!.width };
}

async function pressThroughLoad(page: Page, browserName: string, control: Locator, load: () => Promise<void>) {
  const initial = await bounds(control);
  const point = { x: initial.x + initial.width / 2, y: initial.y + initial.height / 2 };
  const hit = () => control.evaluate((element, at) => element.contains(document.elementFromPoint(at.x, at.y)), point);
  expect(await hit()).toBe(true);
  const touch = browserName === "chromium" ? await page.context().newCDPSession(page) : null;
  try {
    if (touch) await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [point] });
    else {
      await page.mouse.move(point.x, point.y);
      await page.mouse.down();
    }
    await load();
    expect(Math.abs((await bounds(control)).y - initial.y)).toBeLessThanOrEqual(1);
    expect(await hit()).toBe(true);
    if (touch) await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    else await page.mouse.up();
  } finally {
    await touch?.detach();
  }
}

async function expectArrangement(page: Page, layout: { twoColumns: boolean; chartPair: boolean }) {
  const { computers, cpu, memory, team } = widgets(page);
  const [devices, processor, ram, people] = await Promise.all([computers, cpu, memory, team].map(bounds));
  expect(processor.y - (layout.twoColumns ? devices.bottom : people.bottom)).toBeCloseTo(17, 1);
  expect(processor.x).toBeCloseTo(devices.x, 1);
  if (layout.chartPair) {
    expect(ram.y).toBeCloseTo(processor.y, 1);
    expect(ram.x - processor.right).toBeCloseTo(17, 1);
    expect(ram.right).toBeCloseTo(devices.right, 1);
  } else {
    expect(ram.y - processor.bottom).toBeCloseTo(17, 1);
    expect(ram.x).toBeCloseTo(devices.x, 1);
    expect(ram.width).toBeCloseTo(devices.width, 1);
  }
  if (layout.twoColumns) {
    expect(people.y).toBeCloseTo(devices.y, 1);
    expect(people.x - devices.right).toBeCloseTo(17, 1);
  } else {
    expect(people.y - devices.bottom).toBeCloseTo(17, 1);
    expect(people.x).toBeCloseTo(devices.x, 1);
    expect(people.width).toBeCloseTo(devices.width, 1);
  }
}

async function expectBottomSpace(lastLayout: Locator) {
  await expect(lastLayout).toBeVisible();
  const geometry = await lastLayout.evaluate((element) => {
    const scroller = document.scrollingElement!;
    window.scrollTo({ top: scroller.scrollHeight, behavior: "instant" });
    const padding = Number.parseFloat(getComputedStyle(element).paddingBlockEnd);
    return {
      padding,
      viewportGap: window.innerHeight - element.getBoundingClientRect().bottom + padding,
      remainingScroll: scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop,
    };
  });
  expect(geometry.padding).toBeGreaterThanOrEqual(24);
  expect(geometry.viewportGap).toBeGreaterThanOrEqual(geometry.padding - 1);
  expect(geometry.remainingScroll).toBeLessThanOrEqual(1);
}

test("resource history follows the inventory through empty, loading, populated and failed states", async ({ page, request, account }) => {
  const company = await createOrganization(request, account);
  const path = `organizations/${company}`;
  const others = await Promise.all(Array.from({ length: 5 }, (_, index) => makeAccount(request, `Коллега ${index + 1}`)));
  for (const colleague of others) {
    expect((await request.post(`${API}/memberships`, {
      headers: headers(account), data: { company_id: company, user_id: colleague.id },
    })).status()).toBe(201);
  }
  await startSession(page, account);
  await page.setViewportSize({ width: 1720, height: 800 });
  await page.goto(path);
  const { computers, cpu, memory, team } = widgets(page);
  await expect(computers).toContainText("У организации пока нет компьютеров");
  await expect(team.getByRole("article")).toHaveCount(5);
  await expect(cpu).toHaveAttribute("aria-busy", "false");
  await expect(memory).toHaveAttribute("aria-busy", "false");
  await expectArrangement(page, { twoColumns: true, chartPair: true });
  const emptyHeight = (await bounds(computers)).height;
  expect((await bounds(team)).height).toBeGreaterThan((await bounds(computers)).height + (await bounds(cpu)).height);
  await expectBottomSpace(page.locator(".main-content"));

  const inventory = `**/v1/companies/${company}/resources/pool-overview`;
  const cards = computers.locator(".organization-computer-card");
  for (const count of [1, 2, 3]) {
    await addComputer(request, account, `runner-${count}`, company, count > 1);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route(inventory, async (route) => {
      await gate;
      await route.continue();
    });
    try {
      await page.reload();
      await expect(computers).toHaveAttribute("aria-busy", "true");
      await expectArrangement(page, { twoColumns: true, chartPair: true });
      release();
      await expect(cards).toHaveCount(count);
    } finally {
      release();
      await page.unroute(inventory);
    }
    expect((await bounds(computers)).height).toBeGreaterThan(emptyHeight);
    await expectArrangement(page, { twoColumns: true, chartPair: true });
  }

  for (const [width, twoColumns, chartPair] of [[1170, true, false], [800, false, true], [375, false, false]] as const) {
    await page.setViewportSize({ width, height: 800 });
    await expectArrangement(page, { twoColumns, chartPair });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
  await expectBottomSpace(page.locator(".main-content"));
  await page.setViewportSize({ width: 1170, height: 800 });
  const memoryTop = (await bounds(memory)).y - (await bounds(cpu)).y;
  await cpu.locator("summary").click();
  await expect(cpu.getByRole("table")).toBeVisible();
  expect((await bounds(memory)).y - (await bounds(cpu)).y).toBeGreaterThan(memoryTop);
  await expectArrangement(page, { twoColumns: true, chartPair: false });
  await cpu.locator("summary").click();
  await expect(cpu.getByRole("table")).toHaveCount(0);
  await expectArrangement(page, { twoColumns: true, chartPair: false });

  await page.route(inventory, (route) => route.fulfill({ status: 422, json: { detail: "Unavailable inventory" } }));
  await page.reload();
  await expect(computers.getByRole("alert")).toBeVisible();
  await expectArrangement(page, { twoColumns: true, chartPair: false });
});

test("a narrower organization keeps Team after inventory when history is unavailable", async ({ page, request, account }) => {
  const company = await createOrganization(request, account);
  const member = await makeAccount(request, "Читатель команды");
  expect((await request.post(`${API}/memberships`, {
    headers: headers(account), data: { company_id: company, user_id: member.id, permissions: ["members.read"] },
  })).status()).toBe(201);
  await startSession(page, member);
  await page.setViewportSize({ width: 1440, height: 800 });
  await page.goto(`organizations/${company}`);
  const { computers, cpu, memory, team } = widgets(page);
  await expect(team.getByRole("article")).toHaveCount(2);
  await expect(cpu).toHaveCount(0);
  await expect(memory).toHaveCount(0);
  expect((await bounds(team)).y).toBeCloseTo((await bounds(computers)).y, 1);
  await page.setViewportSize({ width: 320, height: 800 });
  await expect.poll(() => page.locator(".organization-widgets").evaluate((layout) => {
    const people = layout.querySelector(".organization-people")!.getBoundingClientRect();
    const inventory = layout.querySelector(".organization-computers")!.getBoundingClientRect();
    return people.top - inventory.bottom;
  })).toBeCloseTo(17, 1);
  await expectBottomSpace(page.locator(".main-content"));
});

for (const target of ["team tab", "member row"] as const) {
  test(`a late chart response keeps an active press on the ${target} on a narrow screen`, async ({ page, request, account, browserName }) => {
    const company = await createOrganization(request, account);
    await startSession(page, account);
    await page.setViewportSize({ width: 390, height: 820 });
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const history = `**/companies/${company}/resources/pool-history`;
    await page.route(history, async (route) => {
      await gate;
      await route.continue();
    });
    try {
      await page.goto(`organizations/${company}`);
      const { computers, cpu, team } = widgets(page);
      await expect(cpu).toHaveAttribute("aria-busy", "true");
      await expect(computers).toHaveAttribute("aria-busy", "false");
      await expect(page.locator(".organization-overview")).toHaveAttribute("aria-busy", "false");
      await page.evaluate(() => document.fonts.ready);
      const control = target === "team tab" ? team.getByRole("tab", { name: /^Приглашения/ })
        : team.locator(".organization-member-summary").first();
      await expect(control).toBeVisible();
      await control.evaluate((element) => element.scrollIntoView({ block: "center", behavior: "instant" }));
      await pressThroughLoad(page, browserName, control, async () => {
        release();
        await expect(cpu).toHaveAttribute("aria-busy", "false");
      });
      await expect(control).toHaveAttribute(target === "team tab" ? "aria-selected" : "aria-expanded", "true");
      await expect.poll(() => computers.evaluate((column) => column.style.blockSize)).toBe("");
      await expectArrangement(page, { twoColumns: false, chartPair: false });
    } finally {
      release();
    }
  });
}

for (const action of ["open", "choose", "open near the edge"] as const) {
  test(`creator role picker can ${action} during a late resource response on mobile`, async ({ page, request, account, browserName }) => {
    const company = await createOrganization(request, account);
    const opening = action !== "choose";
    if (opening) {
      for (const index of [1, 2, 3]) await addComputer(request, account, `runner-${index}`, company, false);
    }
    await startSession(page, account);
    await page.setViewportSize({ width: 390, height: 820 });
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route((url) => opening ? url.pathname === `/v1/companies/${company}/resources/pool-overview`
      : url.pathname === `/v1/companies/${company}/resources/pool-history`, async (route) => {
      await gate;
      await route.continue();
    });
    try {
      await page.goto(`organizations/${company}`);
      const { computers, cpu } = widgets(page);
      await expect(opening ? computers : cpu).toHaveAttribute("aria-busy", "true");
      await expect(page.locator(".organization-overview")).toHaveAttribute("aria-busy", opening ? "true" : "false");
      await switchOrganizationPeople(page, "Приглашения");
      const form = page.getByRole("region", { name: "Добавить участника" });
      await form.locator("summary").click();
      await form.getByLabel("Способ добавления").selectOption("link");
      const role = form.getByLabel("Роль", { exact: true });
      const picker = form.locator('.tmc-select:has(> select[aria-label="Роль"])');
      const face = picker.locator(".tmc-select__face");
      const panel = picker.locator(".tmc-select__panel");
      const option = panel.getByText("Администратор", { exact: true });
      await page.evaluate(() => document.fonts.ready);
      await face.evaluate((element) => element.scrollIntoView({ block: "center", behavior: "instant" }));
      if (action === "open near the edge") {
        await face.evaluate((element) => {
          const box = element.getBoundingClientRect();
          window.scrollBy({ top: box.top + box.height / 2 - 690, behavior: "instant" });
        });
      }
      if (!opening) {
        await face.click();
        await expect(panel).toBeVisible();
      }
      await expect.poll(() => computers.evaluate((element) => element.style.blockSize)).toBe("");
      await pressThroughLoad(page, browserName, opening ? face : option, async () => {
        release();
        await expect(opening ? computers : cpu).toHaveAttribute("aria-busy", "false");
      });
      if (opening) {
        await expect(panel).toBeVisible();
        await expect.poll(() => computers.evaluate((element) => element.style.blockSize)).toBe("");
        if (action === "open near the edge") {
          await expect(panel).toBeInViewport({ ratio: 0.99 });
          await expect(option).toBeInViewport({ ratio: 0.99 });
        } else {
          await expect.poll(() => picker.evaluate((element) => {
            const anchor = element.querySelector(".tmc-select__face")!.getBoundingClientRect();
            const popup = element.querySelector(".tmc-select__panel")!.getBoundingClientRect();
            return Math.min(Math.abs(popup.top - anchor.bottom - 6), Math.abs(anchor.top - popup.bottom - 6));
          })).toBeLessThanOrEqual(1);
        }
        await option.click();
      }
      await expect(role).toHaveValue("admin");
      await expect(role).toBeFocused();
      await expect(panel).toHaveCount(0);
    } finally {
      release();
    }
  });
}

for (const lateWidget of ["charts", "computers"] as const) {
  test(`returning from the full roster keeps the focused team link on screen as ${lateWidget} load`, async ({ page, request, account }) => {
    const company = await createOrganization(request, account);
    const others = await Promise.all(Array.from({ length: 5 }, (_, index) => makeAccount(request, `Участник ${index + 1}`)));
    for (const member of others) {
      expect((await request.post(`${API}/memberships`, {
        headers: headers(account), data: { company_id: company, user_id: member.id },
      })).status()).toBe(201);
    }
    if (lateWidget === "computers") {
      for (const index of [1, 2, 3]) await addComputer(request, account, `Терминал ${index}`, company, false);
    }
    await startSession(page, account);
    await page.setViewportSize(lateWidget === "computers" ? { width: 320, height: 640 } : { width: 390, height: 820 });
    await page.goto(`organizations/${company}`);
    const { team, cpu, computers } = widgets(page);
    const pendingWidget = lateWidget === "computers" ? computers : cpu;
    const full = team.getByRole("link", { name: "Смотреть всех 6", exact: true });
    await expect(pendingWidget).toHaveAttribute("aria-busy", "false");
    await page.evaluate(() => { document.documentElement.style.setProperty("overflow-anchor", "none"); });
    await full.press("Enter");
    await expect(page).toHaveURL(/\/members$/);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route((url) => lateWidget === "computers"
      ? url.pathname === `/v1/companies/${company}/resources/pool-overview`
      : url.pathname === `/v1/companies/${company}/resources/pool-history`, async (route) => {
      await gate;
      await route.continue();
    });
    const linkIsOnScreen = () => full.evaluate((element) => {
      const { x, y, width, height, bottom } = element.getBoundingClientRect();
      return bottom <= innerHeight + 1 && element.contains(document.elementFromPoint(x + width / 2, y + height / 2));
    });
    try {
      await page.goBack();
      await expect(pendingWidget).toHaveAttribute("aria-busy", "true");
      await expect(full).toBeFocused();
      await expect.poll(linkIsOnScreen).toBe(true);
      release();
      await expect(pendingWidget).toHaveAttribute("aria-busy", "false");
      await expect(full).toBeFocused();
      await expect.poll(linkIsOnScreen).toBe(true);
    } finally {
      release();
    }
  });
}

test("scrolling to the end leaves room below auth and account pages", async ({ page, request, account }) => {
  await page.setViewportSize({ width: 390, height: 680 });
  for (const route of ["login", "register"]) {
    await page.goto(route);
    await expectBottomSpace(page.locator(".auth-shell"));
  }
  const company = await createOrganization(request, account);
  await startSession(page, account);
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 680 });
    for (const route of ["", "compute", "compute/computers", "organizations", `organizations/${company}/members`, "profile", "cloud"]) {
      await page.goto(route);
      await expect(page.getByRole("main").getByRole("heading", { level: 1 })).toBeVisible();
      await expectBottomSpace(page.locator(".main-content"));
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("cloud");
  await expect(page.getByRole("main").getByRole("heading", { level: 1 })).toBeVisible();
  expect(await page.evaluate(() => document.scrollingElement!.scrollHeight)).toBe(1000);
});
