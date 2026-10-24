import { test, expect, login } from "./fixtures";
import { actionsPeer, repository } from "./actions-peer";

/** Revoking repositories can shrink a global catalog; refresh must recover from an obsolete page. */
for (const remaining of [0, 1]) {
  test(`Actions clamps catalog pagination after access shrinks to ${remaining} repositories`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    peer.repositories = Array.from({ length: 21 }, (_, i) => ({ ...repository, id: i + 789, full_name: `mutating/repo-${i}` }));
    await login(page, account); await page.goto("/actions?page=2");
    await expect(page.locator(".repository-card")).toHaveCount(1);
    peer.repositories = peer.repositories.slice(0, remaining);
    await page.getByRole("button", { name: "Обновить", exact: true }).click();
    await expect(page).toHaveURL(/page=1/);
    await expect(page.locator(".repository-card")).toHaveCount(remaining);
    expect(peer.calls.some(row => row.path === "github/repositories" && row.query.get("refresh") === "true")).toBe(true);
  });
}
