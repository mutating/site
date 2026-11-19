import { expect, test, type Page } from "@playwright/test";

async function frameWidth(page: Page, width: number) {
  await page.getByTestId("frame").evaluate((frame, size) => { frame.style.inlineSize = `${size}px`; }, width);
}

async function box(page: Page, item: string) {
  const rect = await page.getByTestId(item).boundingBox();
  expect(rect, item).not.toBeNull();
  return rect!;
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1040, height: 920 });
  await page.goto("/widget-layout.html");
  await expect(page.getByTestId("layout")).toBeVisible();
});

test("primary widgets follow their own height with a fractional column weight", async ({ page }) => {
  await frameWidth(page, 960);
  const primary = await box(page, "lead");
  const side = await box(page, "aside");
  expect(primary.width).toBeCloseTo(314, 1);
  expect(side.width).toBeCloseTo(628, 1);
  expect(primary.y).toBeCloseTo(side.y, 1);
  expect(side.x - primary.x - primary.width).toBeCloseTo(18, 1);
  const lead = page.getByTestId("lead");
  for (const height of [62, 420, 88, 62]) {
    await lead.evaluate((element, size) => { element.style.minBlockSize = `${size}px`; }, height);
    const bounds = await box(page, "lead");
    const following = await box(page, "following");
    expect(following.y - bounds.y - bounds.height).toBeCloseTo(18, 1);
    expect((await box(page, "aside")).y).toBeCloseTo(primary.y, 1);
  }
  const initial = await box(page, "following");
  for (const height of [70, 500, 227]) {
    await page.getByTestId("aside").evaluate((element, size) => { element.style.minBlockSize = `${size}px`; }, height);
    expect((await box(page, "following")).y).toBeCloseTo(initial.y, 1);
    const primaryHeight = initial.y + initial.height - primary.y;
    expect((await box(page, "layout")).height).toBeCloseTo(Math.max(height, primaryHeight), 1);
  }
});

test("the compact breakpoint follows container width and preserves order and focus", async ({ page, browserName }) => {
  await frameWidth(page, 832);
  expect((await box(page, "aside")).y).toBeCloseTo((await box(page, "lead")).y, 1);
  const draft = page.getByLabel("Widget draft");
  await draft.fill("Unsaved text");
  for (const width of [831, 310]) {
    await frameWidth(page, width);
    const [lead, side, following] = await Promise.all(["lead", "aside", "following"].map((item) => box(page, item)));
    expect([lead.width, side.width, following.width]).toEqual([width, width, width]);
    expect(side.y - lead.y - lead.height).toBeCloseTo(18, 1);
    expect(following.y - side.y - side.height).toBeCloseTo(18, 1);
  }
  await expect(draft).toHaveValue("Unsaved text");
  await expect(draft).toBeFocused();
  for (const name of ["Side action", "Following action"]) {
    await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
    await expect(page.getByRole("button", { name })).toBeFocused();
  }
});

test("optional groups collapse and a remaining group fills the layout", async ({ page }) => {
  await frameWidth(page, 960);
  const toggleFollowing = page.getByRole("button", { name: "Toggle following" });
  await toggleFollowing.click();
  expect((await box(page, "aside")).y).toBeCloseTo((await box(page, "lead")).y, 1);
  expect((await box(page, "layout")).height).toBeCloseTo(300, 1);
  await page.getByRole("button", { name: "Toggle lead" }).click();
  expect((await box(page, "aside")).width).toBeCloseTo(960, 1);
  await toggleFollowing.click();
  expect((await box(page, "following")).y).toBeCloseTo((await box(page, "aside")).y, 1);
  expect((await box(page, "following")).width).toBeCloseTo(314, 1);
  await page.getByRole("button", { name: "Toggle aside" }).click();
  const following = await box(page, "following");
  expect(following.width).toBeCloseTo(960, 1);
  expect((await box(page, "layout")).height).toBeCloseTo(following.height, 1);
});

test("nested layouts use their own width and fit long text on narrow screens", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 920 });
  await frameWidth(page, 1320);
  const first = await box(page, "nested-first");
  const second = await box(page, "nested-second");
  expect(second.width).toBeCloseTo(first.width, 1);
  expect(second.y).toBeCloseTo(first.y, 1);
  expect(second.x - first.x - first.width).toBeCloseTo(16, 1);
  await frameWidth(page, 960);
  const stacked = await box(page, "nested-first");
  expect((await box(page, "nested-second")).y - stacked.y - stacked.height).toBeCloseTo(16, 1);
  await page.setViewportSize({ width: 320, height: 800 });
  await frameWidth(page, 320);
  const narrowFirst = await box(page, "nested-first");
  const narrowSecond = await box(page, "nested-second");
  expect(narrowFirst.width).toBeCloseTo(320, 1);
  expect(narrowSecond.y - narrowFirst.y - narrowFirst.height).toBeCloseTo(16, 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
});

test("native hidden widgets release their tracks and keep their DOM state", async ({ page }) => {
  await frameWidth(page, 960);
  const side = page.getByTestId("aside");
  const following = page.getByTestId("following");
  await side.evaluate((element) => element.setAttribute("hidden", ""));
  await expect(side).toBeHidden();
  await expect(side).toHaveCount(1);
  expect((await box(page, "lead")).width).toBeCloseTo(960, 1);
  expect((await box(page, "following")).width).toBeCloseTo(960, 1);
  await following.evaluate((element) => element.setAttribute("hidden", ""));
  expect((await box(page, "layout")).height).toBeCloseTo((await box(page, "lead")).height, 1);
  await side.evaluate((element) => element.removeAttribute("hidden"));
  expect((await box(page, "aside")).width).toBeCloseTo(628, 1);
  expect((await box(page, "aside")).y).toBeCloseTo((await box(page, "lead")).y, 1);
});

test("text and visible widgets retain their tracks next to hidden content", async ({ page }) => {
  await frameWidth(page, 960);
  const status = page.getByTestId("text-layout").locator(".tmc-widget-layout__aside");
  await expect(status).toContainText("Queue paused");
  await expect(status).toBeVisible();
  await expect(page.getByTestId("hidden-text-neighbor")).toBeHidden();
  await expect(page.getByTestId("hidden-widget")).toBeHidden();
  const lead = await box(page, "text-lead");
  expect(lead.width).toBeCloseTo(471, 1);
  expect((await status.boundingBox())!.y).toBeCloseTo(lead.y, 1);
  const following = await box(page, "text-following");
  const layout = await box(page, "text-layout");
  expect(layout.y + layout.height).toBeCloseTo(following.y + following.height, 1);
});

test("an inline-size-contained layout uses the available width as a flex item", async ({ page }) => {
  await frameWidth(page, 960);
  const layout = page.getByTestId("layout");
  await layout.evaluate((element) => {
    const parent = document.createElement("div");
    parent.style.display = "flex";
    element.before(parent);
    parent.append(element);
  });
  expect((await box(page, "layout")).width).toBeCloseTo(960, 1);
  expect((await box(page, "aside")).y).toBeCloseTo((await box(page, "lead")).y, 1);
  await frameWidth(page, 380);
  expect((await box(page, "layout")).width).toBeCloseTo(380, 1);
  expect((await box(page, "aside")).y).toBeGreaterThan((await box(page, "lead")).y);
});

test("a nested layout releases and restores its parent track as its last widget changes", async ({ page }) => {
  const nested = page.getByTestId("optional-nested");
  for (const width of [960, 480]) {
    await frameWidth(page, width);
    await expect(nested).toBeVisible();
    if (width === 960) expect((await box(page, "optional-lead")).width).toBeCloseTo(471, 1);
    await nested.getByRole("button", { name: "Remove nested widget" }).click();
    await expect(nested).toBeHidden();
    const lead = await box(page, "optional-lead");
    const following = await box(page, "optional-following");
    expect(lead.width).toBeCloseTo(width, 1);
    expect(following.y - lead.y - lead.height).toBeCloseTo(18, 1);
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("fixture-nested-widget", { detail: "loading" })));
    await expect(nested).toHaveAttribute("data-state", "loading");
    await expect(nested).toBeHidden();
    const loadingLead = await box(page, "optional-lead");
    expect(loadingLead.width).toBeCloseTo(width, 1);
    expect((await box(page, "optional-following")).y - loadingLead.y - loadingLead.height).toBeCloseTo(18, 1);
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("fixture-nested-widget", { detail: "ready" })));
    await expect(nested).toBeVisible();
    if (width === 960) expect((await box(page, "optional-lead")).width).toBeCloseTo(471, 1);
    else expect((await box(page, "optional-following")).y - (await box(page, "optional-nested")).y - (await box(page, "optional-nested")).height).toBeCloseTo(18, 1);
  }
});

test("hidden React Activity releases its track and preserves the widget state", async ({ page }) => {
  const side = page.getByTestId("activity-aside");
  const toggle = page.getByRole("button", { name: "Toggle activity" });
  const draft = page.getByLabel("Activity draft");
  await draft.fill("Preserve this draft");
  for (const width of [960, 480]) {
    await frameWidth(page, width);
    await expect(side).toBeVisible();
    await toggle.click();
    await expect(side).toBeHidden();
    await expect(side).toHaveCount(1);
    const lead = await box(page, "activity-lead");
    expect(lead.width).toBeCloseTo(width, 1);
    expect((await box(page, "activity-following")).y - lead.y - lead.height).toBeCloseTo(18, 1);
    await toggle.click();
    await expect(draft).toBeVisible();
    await expect(draft).toHaveValue("Preserve this draft");
    if (width === 960) expect((await box(page, "activity-lead")).width).toBeCloseTo(471, 1);
  }
});

test("new widgets can focus input during mount in an empty group or layout", async ({ page }) => {
  for (const width of [960, 480]) {
    await frameWidth(page, width);
    for (const name of ["Populated layout", "Empty layout"]) {
      const example = page.getByRole("region", { name, exact: true });
      for (const action of ["Mount autofocus widget", "Mount layout effect widget"]) {
        await example.getByRole("button", { name: action }).press("Enter");
        const input = example.getByLabel("New widget draft");
        await expect(input).toBeFocused();
        await expect(input).toBeInViewport({ ratio: 0.95 });
        await example.getByRole("button", { name: "Unmount focus widget" }).press("Enter");
        await expect(input).toHaveCount(0);
      }
    }
    for (const name of ["Natively hidden layout", "Inline hidden layout", "CSS hidden layout"]) {
      const example = page.getByRole("region", { name, exact: true });
      const trigger = example.getByRole("button", { name: "Mount autofocus widget" });
      await trigger.press("Enter");
      await expect(example.getByLabel("New widget draft")).toBeHidden();
      await expect(trigger).toBeFocused();
      await example.getByRole("button", { name: "Unmount focus widget" }).press("Enter");
      await expect(example.getByLabel("New widget draft")).toHaveCount(0);
    }
  }
});

test("styled empty nested layouts leave no box in their parent grid", async ({ page }) => {
  const example = page.getByRole("region", { name: "Styled nested layouts" });
  const toggle = example.getByRole("button", { name: "Toggle nested widgets" });
  const styled = example.getByRole("button", { name: /styled widget$/ });
  const gap = async () => {
    const lead = await box(page, "styled-lead");
    return (await box(page, "styled-following")).y - lead.y - lead.height;
  };
  for (const width of [960, 480]) {
    await frameWidth(page, width);
    const track = (await box(page, "styled-lead")).width;
    expect(await gap()).toBeCloseTo(18, 1);
    await toggle.press("Enter");
    await expect(styled).toHaveCount(2);
    if (width === 960) expect((await box(page, "styled-lead")).width).toBeLessThan(track);
    else expect(await gap()).toBeGreaterThan(18);
    await toggle.press("Enter");
    await expect(styled).toHaveCount(0);
    await expect.poll(gap).toBeCloseTo(18, 1);
    expect((await box(page, "styled-lead")).width).toBeCloseTo(track, 1);
  }
});

test("tracks follow the inline direction and unsupported gaps keep rows separated", async ({ page, browserName }) => {
  await frameWidth(page, 960);
  const layout = page.getByTestId("layout");
  await layout.evaluate((element) => element.setAttribute("dir", "rtl"));
  const primary = await box(page, "lead");
  const side = await box(page, "aside");
  expect(primary.x - side.x - side.width).toBeCloseTo(18, 1);
  await page.getByLabel("Widget draft").focus();
  await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
  await expect(page.getByRole("button", { name: "Side action" })).toBeFocused();
  await frameWidth(page, 480);
  for (const [value, size] of [["10%", 16], ["calc(1rem + 6px)", 22], ["-8px", 0]] as const) {
    await layout.evaluate((element, gap) => element.style.setProperty("--tmc-widget-layout-gap", gap), value);
    const lead = await box(page, "lead");
    expect((await box(page, "aside")).y - lead.y - lead.height).toBeCloseTo(size, 1);
  }
});
