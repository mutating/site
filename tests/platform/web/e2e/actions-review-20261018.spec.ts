import { test, expect, login } from "./fixtures";
import { actionsPeer, installation, repository } from "./actions-peer";

// Assertions: automatically selecting another installation after suspension resets
// repository pagination. Its first page is visible immediately, without a manual
// recovery click through an unrelated installation's page number.
for (const replacement of ["suspended", "removed"]) {
  test(`Actions resets repository pagination when refresh replaces a ${replacement} owner`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    const alternate = { ...installation, id: 457, login: "other-team" };
    const alternateRepository = { ...repository, id: 790, full_name: "other-team/new-ci" };
    peer.installations = [{ ...installation }, alternate];
    peer.custom = async (route, url) => {
      if (!url.pathname.endsWith("/repositories")) return false;
      const alternateOwner = url.pathname.includes("/457/");
      const pageNumber = url.searchParams.get("page");
      await route.fulfill({ json: alternateOwner
        ? { total_count: 1, repositories: pageNumber === "1" ? [alternateRepository] : [] }
        : { total_count: 101, repositories: [repository] } });
      return true;
    };
    await login(page, account); await page.goto("/actions");
    await expect(page.getByRole("link", { name: repository.full_name, exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Далее", exact: true }).click();
    await expect.poll(() => peer.calls.some((call) => call.path === "github/installations/456/repositories" && call.query.get("page") === "2")).toBe(true);
    peer.installations = replacement === "suspended" ? [{ ...installation, suspended: true }, alternate] : [alternate];
    await page.getByRole("button", { name: "Обновить", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Аккаунт или организация GitHub", exact: true })).toHaveValue("457");
    await expect(page.getByRole("link", { name: alternateRepository.full_name, exact: true })).toBeVisible();
    expect(peer.calls.filter((call) => call.path === "github/installations/457/repositories").map((call) => call.query.get("page"))).toEqual(["1"]);
  });
}
