import type { Page } from "@playwright/test";
import { test, expect, login } from "./fixtures";
import { actionsPeer } from "./actions-peer";

const broad = [1440, 1101, 1024, 801];
const narrow = [800, 700, 640, 540, 480, 430, 390, 360, 320, 280];
const overflowing = [430, 390, 360, 320, 280];

async function settle(page: Page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  });
}

async function navigation(page: Page) {
  return page.locator(".sidebar nav").evaluate((nav) => {
    const style = getComputedStyle(nav);
    const hint = nav.parentElement!.querySelector(".nav-scroll-hint")!;
    const hintStyle = getComputedStyle(hint);
    const navBox = nav.getBoundingClientRect();
    const hintBox = hint.getBoundingClientRect();
    const sidebarBox = nav.closest(".sidebar")!.getBoundingClientRect();
    const mainBox = document.querySelector("main")!.getBoundingClientRect();
    const links = Array.from(nav.querySelectorAll<HTMLElement>(".nav-link"), (link) => {
      const box = link.getBoundingClientRect();
      return {
        top: box.top,
        left: box.left,
        right: box.right,
        bottom: box.bottom,
        touchAction: getComputedStyle(link).touchAction,
      };
    });
    return {
      direction: style.flexDirection,
      wrap: style.flexWrap,
      overflowX: style.overflowX,
      touchAction: style.touchAction,
      scrollWidth: nav.scrollWidth,
      clientWidth: nav.clientWidth,
      scrollLeft: nav.scrollLeft,
      links,
      hint: {
        display: hintStyle.display,
        visibility: hintStyle.visibility,
        opacity: Number(hintStyle.opacity),
        left: hintBox.left,
        right: hintBox.right,
        width: hintBox.width,
        navRight: navBox.right,
      },
      sidebar: { top: sidebarBox.top, bottom: sidebarBox.bottom, right: sidebarBox.right },
      main: { top: mainBox.top, left: mainBox.left },
    };
  });
}

test.describe("responsive account navigation", () => {
  test("desktop keeps the vertical sidebar at every wide breakpoint", async ({ page, account }) => {
    await actionsPeer(page);
    await login(page, account);
    await expect(page.locator(".sidebar .nav-link")).toHaveCount(5);

    for (const width of broad) {
      await page.setViewportSize({ width, height: 1000 });
      await settle(page);
      const nav = await navigation(page);

      expect(nav.direction).toBe("column");
      expect(nav.wrap).toBe("nowrap");
      expect(nav.overflowX).toBe("visible");
      expect(nav.hint.display).toBe("none");
      expect(nav.scrollWidth).toBeLessThanOrEqual(nav.clientWidth + 1);
      expect(new Set(nav.links.map((link) => Math.round(link.top))).size).toBe(5);
      expect(new Set(nav.links.map((link) => Math.round(link.left))).size).toBe(1);
      expect(nav.sidebar.right).toBeLessThanOrEqual(nav.main.left + 1);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  });

  test("narrow screens keep navigation on one scrollable row with a right hint", async ({
    page,
    account,
  }) => {
    await actionsPeer(page);
    await login(page, account);
    await expect(page.locator(".sidebar .nav-link")).toHaveCount(5);

    for (const width of narrow) {
      await page.setViewportSize({ width, height: 900 });
      await settle(page);
      const nav = await navigation(page);
      const overflow = nav.scrollWidth > nav.clientWidth + 1;

      expect(nav.direction).toBe("row");
      expect(nav.wrap).toBe("nowrap");
      expect(nav.overflowX).toBe("auto");
      expect(nav.touchAction).toBe("auto");
      expect(nav.links.every((link) => link.touchAction === "manipulation")).toBe(true);
      expect(new Set(nav.links.map((link) => Math.round(link.top))).size).toBe(1);
      expect(nav.sidebar.bottom).toBeLessThanOrEqual(nav.main.top + 1);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );

      if (overflowing.includes(width)) {
        expect(overflow).toBe(true);
      }
      if (overflow) {
        expect(nav.hint.display).toBe("block");
        await expect
          .poll(async () => (await navigation(page)).hint.visibility)
          .toBe("visible");
        await expect
          .poll(async () => (await navigation(page)).hint.opacity)
          .toBeGreaterThan(0.9);
        const hinted = await navigation(page);
        expect(hinted.hint.right).toBeGreaterThanOrEqual(hinted.hint.navRight - 1);
        expect(hinted.hint.left).toBeLessThan(hinted.hint.navRight);
      } else {
        expect(nav.hint.visibility).toBe("hidden");
      }

      if (overflow) {
        const scroller = page.locator(".sidebar nav");
        await scroller.evaluate((element) => {
          element.scrollLeft = element.scrollWidth;
        });
        await expect.poll(() => scroller.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
        await expect
          .poll(async () => (await navigation(page)).hint.visibility)
          .toBe("hidden");
        await scroller.evaluate((element) => {
          element.scrollLeft = 0;
        });
        await expect
          .poll(async () => (await navigation(page)).hint.visibility)
          .toBe("visible");
      }
    }
  });

  test("vertical mouse wheel scrolls an overflowing row and leaves the page still", async ({
    page,
    account,
  }, info) => {
    test.skip(info.project.use.hasTouch === true, "Touch devices use native horizontal panning");
    await actionsPeer(page);
    await login(page, account);
    await page.setViewportSize({ width: 390, height: 900 });
    await settle(page);

    const scroller = page.locator(".sidebar nav");
    expect(await scroller.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(
      true,
    );
    await scroller.evaluate((element) => {
      element.scrollLeft = 0;
    });
    const box = (await scroller.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    const pageY = await page.evaluate(() => window.scrollY);
    await page.mouse.wheel(0, 40);
    await expect.poll(() => scroller.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
    expect(await page.evaluate(() => window.scrollY)).toBe(pageY);
  });

  test("a finger pans the row horizontally without blocking vertical page scroll", async ({
    page,
    account,
    browserName,
    isMobile,
  }) => {
    test.skip(
      browserName !== "chromium" || !isMobile,
      "Requires the mobile Chromium touch protocol",
    );
    await actionsPeer(page);
    await login(page, account);
    await page.setViewportSize({ width: 390, height: 844 });
    await settle(page);

    const scroller = page.locator(".sidebar nav");
    expect(await scroller.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(
      true,
    );
    const box = (await scroller.boundingBox())!;
    const touch = await page.context().newCDPSession(page);
    const swipe = async (deltaX: number, deltaY: number) => {
      const startX = box.x + box.width - 40;
      const startY = box.y + box.height / 2;
      await touch.send("Input.dispatchTouchEvent", {
        type: "touchStart",
        touchPoints: [{ x: startX, y: startY }],
      });
      for (let step = 1; step <= 6; step += 1) {
        await touch.send("Input.dispatchTouchEvent", {
          type: "touchMove",
          touchPoints: [
            {
              x: startX + (deltaX * step) / 6,
              y: startY + (deltaY * step) / 6,
            },
          ],
        });
        await page.waitForTimeout(20);
      }
      await touch.send("Input.dispatchTouchEvent", {
        type: "touchEnd",
        touchPoints: [],
      });
    };

    try {
      await swipe(-140, 0);
      await expect
        .poll(() => scroller.evaluate((element) => element.scrollLeft))
        .toBeGreaterThan(0);

      // Let touch momentum settle before checking a perpendicular gesture.
      await page.waitForTimeout(300);
      await scroller.evaluate((element) => {
        element.scrollLeft = 0;
      });
      await page.evaluate(() => window.scrollTo(0, 0));
      await swipe(0, -140);
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
    } finally {
      await touch.detach();
    }
  });

  test("resizing crosses the breakpoint in both directions", async ({ page, account }) => {
    await actionsPeer(page);
    await login(page, account);

    await page.setViewportSize({ width: 1440, height: 1000 });
    await settle(page);
    expect((await navigation(page)).direction).toBe("column");

    await page.setViewportSize({ width: 800, height: 1000 });
    await settle(page);
    let nav = await navigation(page);
    expect(nav.direction).toBe("row");
    expect(nav.scrollWidth).toBeLessThanOrEqual(nav.clientWidth + 1);
    expect(nav.hint.visibility).toBe("hidden");

    await page.setViewportSize({ width: 390, height: 1000 });
    await settle(page);
    nav = await navigation(page);
    expect(nav.direction).toBe("row");
    expect(nav.scrollWidth).toBeGreaterThan(nav.clientWidth);
    await expect
      .poll(async () => (await navigation(page)).hint.visibility)
      .toBe("visible");

    await page.setViewportSize({ width: 1440, height: 1000 });
    await settle(page);
    nav = await navigation(page);
    expect(nav.direction).toBe("column");
    expect(nav.scrollLeft).toBe(0);
    expect(nav.hint.display).toBe("none");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });

  test("narrow navigation still navigates and marks the active section", async ({
    page,
    account,
  }) => {
    await actionsPeer(page);
    await login(page, account);
    await page.setViewportSize({ width: 320, height: 700 });
    await settle(page);

    const organizations = page
      .getByRole("navigation")
      .getByRole("link", { name: "Организации", exact: true });
    await organizations.click();
    await expect(page).toHaveURL("http://127.0.0.1:18101/organizations");
    await expect(organizations).toHaveClass(/active/);
    await expect(organizations).toBeInViewport({ ratio: 0.99 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
});
