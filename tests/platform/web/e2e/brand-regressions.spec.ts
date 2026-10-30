import { expect, test } from "./fixtures";

test("the visible edge of the unfolding wordmark remains a native link", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.context().route("https://mutating.tech/**", (route) => route.fulfill({
    contentType: "text/html", body: "<title>Main website</title>",
  }));
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const wordmark = page.locator(".tmc-brand__wordmark");
  await wordmark.hover();
  // Hold a real intermediate CSS transition frame so slower runners exercise
  // the same visible edge instead of accidentally waiting for the final font.
  const edge = await wordmark.evaluate((element) => {
    for (const animation of document.getAnimations()) {
      const target = (animation.effect as KeyframeEffect | null)?.target;
      if (!(target instanceof Element) || !element.contains(target)) continue;
      animation.pause();
      animation.currentTime = 100;
    }
    const text = element.getBoundingClientRect();
    const x = text.right - 2;
    const y = text.y + text.height / 2;
    return {
      x, y,
      hitIsBrand: !!document.elementFromPoint(x, y)?.closest(".tmc-brand"),
    };
  });
  expect(edge.hitIsBrand).toBe(true);
  await page.mouse.move(edge.x, edge.y);
  await expect(wordmark).toHaveAttribute("data-hover-expanded", "true", { timeout: 1000 });
  await page.mouse.down();
  await page.mouse.up();
  await expect(page).toHaveURL("https://mutating.tech/ru/");
});

for (const exit of ["pointerout", "blur", "pointer-center"] as const) {
  test(`reduced-motion gaze recenters after ${exit} and follows the next pointer move`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 1440, height: 800 });
    await page.goto("login");
    await expect(page.locator(".auth-panel")).toBeVisible();
    const gaze = () => page.locator(".tmc-eye__pupil").evaluate((pupil) => {
      const style = getComputedStyle(pupil);
      return ["--gaze-x", "--gaze-y", "--gaze-tilt"].map((name) => Number.parseFloat(style.getPropertyValue(name)));
    });
    await page.mouse.move(1300, 500);
    await expect.poll(async () => (await gaze())[0]).toBeGreaterThan(2);
    if (exit === "pointerout") await page.mouse.move(1500, 500);
    else if (exit === "blur") await page.evaluate(() => window.dispatchEvent(new Event("blur")));
    else {
      const eye = (await page.locator(".tmc-eye").boundingBox())!;
      await page.mouse.move(eye.x + eye.width / 2, eye.y + eye.height / 2);
    }
    await expect.poll(async () => (await gaze()).every((value) => Math.abs(value) < 0.1), { timeout: 1000 }).toBe(true);
    await page.mouse.move(1, 50);
    await expect.poll(async () => (await gaze())[0]).toBeLessThan(-1);
  });
}

test("gaze follows motion preference changes without reloading the header", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
  await page.clock.pauseAt(new Date("2026-01-01T00:00:01Z"));
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.setViewportSize({ width: 1440, height: 800 });
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  await page.clock.runFor(1000);
  await page.evaluate(() => {
    matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", (event) => {
      document.body.dataset.motionPreferenceDelivered = String(event.matches);
    });
  });
  const gazeX = () => page.locator(".tmc-eye__pupil").evaluate((pupil) =>
    Number.parseFloat(getComputedStyle(pupil).getPropertyValue("--gaze-x")),
  );
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.clock.runFor(32);
  await expect(page.locator("body")).toHaveAttribute("data-motion-preference-delivered", "true");
  // No animation frames elapse during mouse input: reduced motion must apply
  // the new target directly, including after a live OS preference change.
  await page.mouse.move(1300, 500);
  await expect.poll(gazeX, { timeout: 1000 }).toBeGreaterThan(2);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.clock.runFor(32);
  await expect(page.locator("body")).toHaveAttribute("data-motion-preference-delivered", "false");
  await page.mouse.move(1, 50);
  expect(await gazeX()).toBeGreaterThan(2);
  await page.clock.runFor(1500);
  await expect.poll(gazeX).toBeLessThan(-1);
});

for (const motion of ["no-preference", "reduce"] as const) {
  /** Resize must retarget a stationary pointer in both directions and preserve
   * recentering after pointer exit. Advance animation frames explicitly: a busy
   * CI host can deliver too few frames within the real-time assertion deadline.
   */
  test(`gaze follows a stationary pointer after resize with ${motion} motion`, async ({ page }) => {
    await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
    await page.clock.pauseAt(new Date("2026-01-01T00:00:01Z"));
    await page.emulateMedia({ reducedMotion: motion });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("login");
    await expect(page.locator(".auth-panel")).toBeVisible();
    const gazeX = () => page.locator(".tmc-eye__pupil").evaluate((pupil) =>
      Number.parseFloat(getComputedStyle(pupil).getPropertyValue("--gaze-x")),
    );
    await page.mouse.move(190, 50);
    await page.clock.runFor(1000);
    await expect.poll(gazeX).toBeGreaterThan(2);
    // The centered header moves to the other side of the stationary pointer.
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.clock.runFor(1000);
    await expect.poll(gazeX, { timeout: 2000 }).toBeLessThan(-1);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.clock.runFor(1000);
    await expect.poll(gazeX).toBeGreaterThan(2);
    await page.mouse.move(2000, 50);
    await page.clock.runFor(1000);
    await expect.poll(async () => Math.abs(await gazeX())).toBeLessThan(0.1);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.clock.runFor(1000);
    await expect.poll(async () => Math.abs(await gazeX())).toBeLessThan(0.1);
  });
}
