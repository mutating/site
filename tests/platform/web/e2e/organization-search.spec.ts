import { test, expect, login, API, headers } from "./fixtures";

test("server pagination searches all organizations and URL state survives reload and both ways back", async ({
  page,
  request,
  account,
}) => {
  for (let i = 0; i < 23; i++) {
    const response = await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name: `Team ${i}` },
    });
    expect(response.status()).toBe(201);
  }
  await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Other space" },
  });
  await login(page, account);
  const directoryCalls: URL[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/v1/organization-directory?"))
      directoryCalls.push(new URL(req.url()));
  });
  await page.goto("organizations?q=Team&page=2");
  const expected = ["Team 20", "Team 21", "Team 22"];
  await expect(page.locator(".organization-row h3")).toHaveText(expected);
  await expect(page.getByRole("searchbox")).toHaveValue("Team");
  await expect(page.locator(".directory-count")).toHaveText("23 / 24");
  expect(directoryCalls[0].searchParams.get("offset")).toBe("20");
  expect(directoryCalls[0].searchParams.get("limit")).toBe("20");
  await expect(page.getByRole("button", { name: /Далее/ })).toBeDisabled();
  await page.reload();
  await expect(page.locator(".organization-row h3")).toHaveText(expected);
  await page
    .getByRole("link", { name: "Открыть организацию: Team 20", exact: true })
    .click();
  await page.goBack();
  await expect(page.locator(".organization-row h3")).toHaveText(expected);
  await page
    .getByRole("link", { name: "Открыть организацию: Team 20", exact: true })
    .click();
  await page
    .getByRole("link", { name: "← К организациям", exact: true })
    .click();
  await expect(page).toHaveURL(/organizations\?q=Team&page=2$/);
  await expect(page.locator(".organization-row h3")).toHaveText(expected);
  await page.getByRole("searchbox").fill("Team 22");
  await expect(page.locator(".organization-row h3")).toHaveText(["Team 22"]);
  expect(new URL(page.url()).searchParams.has("page")).toBe(false);
  await expect(page.locator(".directory-count")).toHaveText("1 / 24");
  await page.getByRole("button", { name: "Очистить поиск" }).click();
  await expect(page.locator(".organization-row")).toHaveCount(20);
  expect(new URL(page.url()).searchParams.has("q")).toBe(false);
  await expect(page.locator(".directory-count")).toHaveText("24");
  await page.getByRole("button", { name: /Далее/ }).click();
  await expect(page.locator(".organization-row")).toHaveCount(4);
  await page.getByRole("button", { name: /Назад/ }).click();
  await expect(page.locator(".organization-row")).toHaveCount(20);
});

test("typing is debounced for 250 ms and clearing starts an immediate unfiltered request", async ({
  page,
  request,
  account,
}) => {
  await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Laboratory" },
  });
  await login(page, account);
  await page.goto("organizations");
  await expect(page.locator(".organization-row")).toHaveCount(1);
  await page.clock.install({ time: new Date("2026-10-15T00:00:00Z") });
  await page.clock.pauseAt(new Date("2026-10-15T00:00:01Z"));
  const queries: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/v1/organization-directory?"))
      queries.push(new URL(req.url()).searchParams.get("q") || "");
  });
  const search = page.getByRole("searchbox");
  await search.fill("L");
  await page.clock.runFor(100);
  await search.fill("La");
  await page.clock.runFor(100);
  await search.fill("Lab");
  await page.clock.runFor(249);
  expect(queries).toEqual([]);
  await expect(page.locator(".organization-directory")).toHaveAttribute(
    "aria-busy",
    "true",
  );
  await page.clock.runFor(1);
  await expect.poll(() => queries).toEqual(["Lab"]);
  await expect(page.locator(".organization-row h3")).toHaveText(["Laboratory"]);
  await search.fill("not sent");
  await page.clock.runFor(100);
  await page.getByRole("button", { name: "Очистить поиск" }).click();
  await page.clock.runFor(1);
  await expect.poll(() => queries).toEqual(["Lab", ""]);
  await page.clock.runFor(300);
  expect(queries).toEqual(["Lab", ""]);
  await expect(page.locator(".organization-row h3")).toHaveText(["Laboratory"]);
});

/** Deliver a real obsolete response over an independently live transport after cancellation; the newer directory data must remain authoritative. */
test("a delayed obsolete response cannot replace newer results even when transport ignores abort", async ({
  page,
  request,
  account,
}) => {
  for (const name of ["Old laboratory", "New laboratory"])
    await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name },
    });
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    const aborted: string[] = [];
    const delivered: { query: string; callerAborted: boolean; transportAborted: boolean; status: number; names: string[] }[] = [];
    Object.defineProperty(window, "__directoryAborted", { value: aborted });
    Object.defineProperty(window, "__directoryDelivered", { value: delivered });
    window.fetch = async (input, options) => {
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      if (url.pathname !== "/v1/organization-directory") return original(input, options);
      const query = url.searchParams.get("q") || "";
      const signal = options?.signal ?? (input instanceof Request ? input.signal : undefined);
      signal?.addEventListener("abort", () => aborted.push(query), { once: true });
      // undefined would inherit Request.signal; override it with an independent
      // live signal so the old HTTP response really reaches the browser.
      const transport = new AbortController();
      const response = await original(input, { ...options, signal: transport.signal });
      const body = await response.clone().json();
      delivered.push({
        query, callerAborted: signal?.aborted ?? false, transportAborted: transport.signal.aborted,
        status: response.status, names: body.items.map((item: any) => item.company?.name ?? item.invitation.company_name),
      });
      return response;
    };
  });
  await login(page, account);
  await page.goto("organizations");
  await expect(page.locator(".organization-row")).toHaveCount(2);
  let release!: () => void, received!: () => void, delivered!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const oldReceived = new Promise<void>((resolve) => {
    received = resolve;
  });
  const oldDelivered = new Promise<void>((resolve) => {
    delivered = resolve;
  });
  await page.route("**/v1/organization-directory?*", async (route) => {
    if (new URL(route.request().url()).searchParams.get("q") !== "Old")
      return route.continue();
    const response = await route.fetch();
    received();
    await gate;
    await route.fulfill({ response });
    delivered();
  });
  try {
    await page.getByRole("searchbox").fill("Old");
    await oldReceived;
    await page.getByRole("searchbox").fill("New");
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            (window as unknown as { __directoryAborted: string[] })
              .__directoryAborted,
        ),
      )
      .toContain("Old");
    await expect(page.locator(".organization-row h3")).toHaveText([
      "New laboratory",
    ]);
    release();
    await oldDelivered;
    await expect.poll(() => page.evaluate(() => (window as any).__directoryDelivered.find((row: any) => row.query === "Old"))).toEqual({
      query: "Old", callerAborted: true, transportAborted: false, status: 200, names: ["Old laboratory"],
    });
    await page.waitForLoadState("networkidle");
    await expect(page.locator(".organization-row h3")).toHaveText([
      "New laboratory",
    ]);
    await expect(page.locator(".directory-count")).toHaveText("1 / 2");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.locator(".organization-directory")).toHaveAttribute(
      "aria-busy",
      "false",
    );
  } finally {
    release();
  }
});

test("search retries transport failures automatically, retaining the query without claiming no matches", async ({
  page,
  request,
  account,
}) => {
  await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Retry lab" },
  });
  await login(page, account);
  await page.goto("organizations");
  await expect(page.locator(".organization-row")).toHaveCount(1);
  let fail = true;
  let attempts = 0;
  await page.route("**/v1/organization-directory?*", (route) => {
    ++attempts;
    return fail ? route.abort("failed") : route.continue();
  });
  await page.getByRole("searchbox").fill("retry");
  await expect.poll(() => attempts).toBeGreaterThan(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Организации не найдены", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".directory-count")).toHaveText("—");
  await expect(page.getByRole("searchbox")).toHaveValue("retry");
  expect(new URL(page.url()).searchParams.get("q")).toBe("retry");
  fail = false;
  await expect(page.locator(".organization-row h3")).toHaveText(["Retry lab"]);
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(attempts).toBeGreaterThan(1);
  await page.getByRole("searchbox").fill("missing");
  await expect(
    page.getByRole("heading", { name: "Организации не найдены", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".directory-count")).toHaveText("0 / 1");
});

test("removing the last search result page corrects the URL without losing the query", async ({
  page,
  request,
  account,
}) => {
  const ids: string[] = [];
  for (let i = 0; i < 21; i++) {
    const response = await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name: `Space ${i}` },
    });
    ids.push((await response.json()).id);
  }
  await login(page, account);
  await page.goto("organizations?q=Space&page=2");
  await expect(page.locator(".organization-row h3")).toHaveText(["Space 20"]);
  expect(
    (
      await request.delete(`${API}/companies/${ids[20]}`, {
        headers: headers(account),
      })
    ).status(),
  ).toBe(204);
  await page.reload();
  await expect(page.locator(".organization-row")).toHaveCount(20);
  expect(new URL(page.url()).searchParams.get("q")).toBe("Space");
  expect(new URL(page.url()).searchParams.has("page")).toBe(false);
});
