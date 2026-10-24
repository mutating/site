import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repositoryURL } from "./actions-peer";

/** Inventory visibility is a separate permission; it must not block authorized CI edits or erase saved computer restrictions. */
for (const restricted of [false, true]) {
  test(`CI pool edits without inventory permission preserve computer policy (${restricted})`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    peer.companies = [{ id: "lab", name: "Лаборатория", my_permissions: ["execution.read", "execution.run", "resources.manage"] }];
    peer.bindings = [{ ...binding, company_id: "lab", computer_ids: restricted ? ["default-cell"] : [], workflow_profiles: { ".github/workflows/deploy.yml": { computer_ids: restricted ? ["profile-cell"] : [] } } }];
    peer.failures.set("GET computers", [403]);
    await login(page, account); await page.goto(repositoryURL);
    await page.getByLabel("CPU", { exact: true }).fill("1");
    const save = page.getByRole("button", { name: "Сохранить настройки", exact: true });
    await expect(save).toBeEnabled();
    await save.click();
    await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
    expect(peer.calls.some(row => row.path === "computers" && row.query.get("company_id") === "lab")).toBe(false);
    expect(peer.bindings[0]).toMatchObject({ cpu_millis: 1000, computer_ids: restricted ? ["default-cell"] : [], workflow_profiles: { ".github/workflows/deploy.yml": { computer_ids: restricted ? ["profile-cell"] : [] } } });
    await page.locator(".binding-advanced > summary").click();
    await expect(page.getByLabel("Конкретные компьютеры", { exact: true })).toBeDisabled();
    await expect(page.locator(".workflow-profiles")).toContainText("Нет права просматривать компьютеры");
  });
}
