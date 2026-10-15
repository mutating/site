import { randomUUID } from "node:crypto";
import { browserAPI, apiURL, expect, origin, test } from "./stack";

test("nginx serves root dashboard routes over TLS, retains sessions after restart and recovers from API outage", async ({
  page,
  request,
  stack,
}) => {
  const identity = `https_${randomUUID().replaceAll("-", "")}`;
  const email = `${identity}@example.com`,
    password = "deployment test password";
  expect(
    (
      await request.post(`${apiURL}/users`, {
        data: { email, password, first_name: "Алиса" },
      })
    ).status(),
  ).toBe(201);
  const direct = await request.post(`${apiURL}/auth/login`, {
    data: { login: email, password },
  });
  const token = (await direct.json()).access_token;
  const headers = { Authorization: `Bearer ${token}` };
  const before = await (
    await request.get(`${apiURL}/access-tokens`, { headers })
  ).json();
  for (const path of [
    "/account",
    "/account/",
    "/account/profile",
    "/account/api/session",
  ]) {
    const response = await request.get(`${origin}${path}`, { maxRedirects: 0 });
    expect(response.status()).toBe(404);
    expect(response.headers().location).toBeUndefined();
  }
  await page.goto(`${origin}/`);
  await expect(page.locator("h1").first()).toBeVisible();
  await page.evaluate(() => {
    localStorage.setItem("tmc-language", "en");
    localStorage.setItem("tmc-theme", "light");
  });
  const failures: string[] = [];
  page.on("response", (response) => {
    if (
      (response.url().startsWith(`${origin}/`) || response.url().startsWith(browserAPI)) &&
      response.status() >= 400
    )
      failures.push(response.url());
  });
  await page.goto("power-cells");
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page.getByLabel("Email or nickname").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  const sessionResponse = page.waitForResponse(
    (r) =>
      r.url().endsWith("/auth/session") &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const body = await (await sessionResponse).json();
  expect(body.email).toBe(email);
  expect(body).not.toHaveProperty("access_token");
  await expect(page).toHaveURL(`${origin}/power-cells`);
  await expect(page.getByTestId("computer-count")).toHaveText("0");
  await page.evaluate(() => document.fonts.ready);
  const cookie = (await page.context().cookies()).find(
    (c) => c.name === "tmc_session",
  )!;
  expect(cookie).toMatchObject({
    httpOnly: true,
    secure: true,
    sameSite: "Lax",
    path: "/v1/",
  });
  expect(cookie.expires).toBeGreaterThan(Date.now() / 1000);
  expect(await page.evaluate(() => document.cookie)).not.toContain(cookie.name);
  expect(
    await page.evaluate(() => JSON.stringify({ ...localStorage })),
  ).not.toContain("thr_");
  // The initial anonymous session request is expected; all static assets must load over HTTPS.
  expect(failures.filter((url) => !url.endsWith("/auth/session"))).toEqual([]);
  const assets = await page
    .locator('script[src], link[rel="stylesheet"]')
    .evaluateAll((nodes) =>
      nodes.map(
        (node) => node.getAttribute("src") || node.getAttribute("href")!,
      ),
    );
  expect(assets.length).toBeGreaterThanOrEqual(2);
  for (const asset of assets) {
    expect(asset).toMatch(/^\/(assets\/|preferences\.js$|config\.js$)/);
    const response = await request.get(`${origin}${asset}`);
    expect(response.status()).toBe(200);
    expect(response.headers()["content-type"]).not.toContain("text/html");
  }
  expect((await request.get(`${origin}/missing.js`)).status()).toBe(
    404,
  );
  expect((await request.get(`${origin}/api/session`)).status()).toBe(404);
  const changed = await page.evaluate(
    async () =>
      (
        await fetch(window.__TMC_API_URL__ + "/companies", {
          credentials: "include",
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "ThrongDashboard",
          },
          body: JSON.stringify({ name: "HTTPS organization" }),
        })
      ).status,
  );
  expect(changed).toBe(201);
  const forbidden = await request.post(`${browserAPI}/companies`, {
    headers: {
      Origin: "https://evil.example",
      "X-Requested-With": "ThrongDashboard",
      Cookie: `${cookie.name}=${cookie.value}`,
    },
    data: { name: "forbidden" },
  });
  expect(forbidden.status()).toBe(403);
  await stack.restartDashboard();
  await page.reload();
  await expect(
    page
      .getByRole("region", { name: "HTTPS organization" })
      .getByTestId("computer-count"),
  ).toHaveText("0");
  expect(
    (await page.context().cookies()).find((c) => c.name === cookie.name)!.value,
  ).toBe(cookie.value);
  await page.goto("profile");
  await expect(page.getByLabel("First name", { exact: true })).toHaveValue(
    "Алиса",
  );
  await page.locator("input[type=file]").setInputFiles({
    name: "avatar.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await expect(page.locator(".avatar.large img")).toBeVisible();
  await expect
    .poll(() =>
      page
        .locator(".avatar.large img")
        .evaluate((img: HTMLImageElement) => img.naturalWidth),
    )
    .toBeGreaterThan(0);
  const avatar = await page.request.get(
    `${browserAPI}/users/me/avatar`,
  );
  expect(avatar.status()).toBe(200);
  expect(avatar.headers()["content-type"]).toMatch(/^image\//);
  await stack.stopAPI();
  await page.getByLabel("First name", { exact: true }).fill("После сбоя");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  expect(
    (await page.context().cookies()).some((c) => c.name === cookie.name),
  ).toBe(true);
  await stack.startAPI();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(
    page.locator(".profile-panel form").getByRole("status"),
  ).toHaveText("Changes saved.");
  await expect(
    page.getByRole("link", { name: "The Mutating Company", exact: true }).first(),
  ).toHaveAttribute("href", "https://mutating.tech/");
  await expect(page.locator(".page-footer a")).toHaveAttribute(
    "href",
    "https://mutating.tech/",
  );
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Overview", exact: true })
    .click();
  await expect(page).toHaveURL(`${origin}/`);
  await expect(page.getByRole("heading", { name: "Hello, После сбоя" })).toBeVisible();
  const siteRequest = page.waitForRequest((r) => r.isNavigationRequest());
  await page.reload();
  expect((await (await siteRequest).allHeaders()).cookie || "").not.toContain(
    cookie.name,
  );
  await page.goto("profile");
  await page
    .getByRole("main")
    .getByRole("button", { name: "Sign out", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
  expect(
    (await page.context().cookies()).some((c) => c.name === cookie.name),
  ).toBe(false);
  expect(
    await (await request.get(`${apiURL}/access-tokens`, { headers })).json(),
  ).toEqual(before);
  expect(
    (
      await request.get(`${browserAPI}/auth/session`, {
        headers: { Cookie: `${cookie.name}=${cookie.value}` },
      })
    ).status(),
  ).toBe(401);
  expect(
    (
      await request.post(`${apiURL.replace("/v1", "")}/__test__/history`, {
        data: { computer_id: randomUUID() },
      })
    ).status(),
  ).toBe(404);
});
