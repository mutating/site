import { selectLanguage } from "./header-controls";
import { execFileSync } from "node:child_process";
import { test, expect, login } from "./fixtures";
import { actionsPeer, binding } from "./actions-peer";

// The browser sees the platform's actual generated YAML, rather than a second
// hand-written template that could hide a generator regression.
const workflow = execFileSync("../api/.venv/bin/python", ["-c", `
from types import SimpleNamespace
from throng_api.ci_workflow import render
print(render(SimpleNamespace(id="${binding.id}", ref="refs/heads/main",
    workflow_path=".github/workflows/ci.yml", job_key="test",
    preparation_seconds=300, timeout_seconds=3600), "https://api.example"))
`], { encoding: "utf8" });

// Assertions: users must copy the matrix and gate every CI step; the delivered
// YAML keeps hosted observation separate and enables native sibling cancellation.
test("Actions explains the queue guard and keeps all CI steps on the workload leg", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.bindings = [{ ...binding }];
  peer.custom = async (route, url) => {
    if (!url.pathname.endsWith("/workflow")) return false;
    await route.fulfill({ json: { yaml: workflow } }); return true;
  };
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  const detail = page.locator(".actions-ci-detail");
  await expect(detail).toContainText(/Скопируйте матрицу.*workload.*каждого.*CI.*шага/);
  await expect(detail).toContainText(/queue_guard.*только наблюдает/);
  const yaml = page.getByRole("textbox", { name: "Workflow YAML", exact: true });
  await expect(yaml).toHaveValue(/fail-fast: true/);
  await expect(yaml).toHaveValue(/max-parallel: 2/);
  await expect(yaml).toHaveValue(/if: matrix.role == 'queue_guard'/);
  await expect(yaml).toHaveValue(/if: matrix.role == 'workload'/);
  await expect(yaml).toHaveValue(/POWERCELL_MODE: watch/);
  await selectLanguage(page);
  await expect(detail).toContainText(/Copy the matrix.*workload.*every CI step/);
  await expect(detail).toContainText(/queue_guard.*only observes/);
});
