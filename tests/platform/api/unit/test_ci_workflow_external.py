"""Generated routing steps obey GitHub's inherited workflow execution defaults."""

import io
import json
import runpy
import urllib.request
from contextlib import closing
from types import SimpleNamespace

import yaml

from throng_api.ci_workflow import render


def working_directory(workflow, job, step, workspace, temporary):
    """Resolve documented step, job and workflow precedence at the runner boundary."""
    value = step.get(
        "working-directory",
        job.get("defaults", {})
        .get("run", {})
        .get(
            "working-directory",
            workflow.get("defaults", {}).get("run", {}).get("working-directory", "."),
        ),
    )
    if value == "${{ runner.temp }}":
        return temporary
    return workspace / value


# Assertions: merging the generated router into a monorepo workflow must not
# inherit a missing checkout-relative directory. The exact embedded bootstrap
# starts, receives a confirmed Power Cell through mocked HTTPS and writes its output;
# the original target job keeps its own working-directory setting.
def test_router_starts_independently_of_inherited_working_directory(tmp_path, monkeypatch):
    """Run the delivered Python despite workflow defaults that require checkout."""
    route = SimpleNamespace(
        id="01234567-89ab-cdef-0123-456789abcdef",
        ref="refs/heads/main",
        workflow_path=".github/workflows/ci.yml",
        job_key="test",
        preparation_seconds=90,
        timeout_seconds=3600,
    )
    workflow = yaml.safe_load(render(route, "https://api.example"))
    workflow["defaults"] = {"run": {"working-directory": "./packages/api"}}
    target = workflow["jobs"][route.job_key]
    target["defaults"] = {"run": {"working-directory": "./target-job"}}
    router = workflow["jobs"][target["needs"]]
    step = router["steps"][0]
    workspace, temporary = tmp_path / "workspace", tmp_path / "runner_temp"
    workspace.mkdir()
    temporary.mkdir()
    output = temporary / "github_output"
    for name, value in {
        **step["env"],
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-credential",
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("POWERCELL_INSTANCE", raising=False)
    calls = []
    selection = ["powercell-one"]

    class Peer:
        """Replace external HTTPS while preserving the generated JSON transport."""

        def open(self, request, timeout):
            calls.append(request.full_url.rsplit("/", 1)[-1].split("?", 1)[0])
            assert timeout == (25 if "wait_seconds=" in request.full_url else 5)
            if calls[-1] == "token":
                assert request.get_header("Authorization") == "Bearer request-credential"
                body = {"value": "oidc-identity"}
            else:
                assert request.get_header("Authorization") == "Bearer oidc-identity"
                assert json.loads(request.data) == {"instance": "default"}
                body = {"status": "ready", "runs_on": selection}
            return closing(io.BytesIO(json.dumps(body).encode()))

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Peer())
    assert not (workspace / "packages/api").exists()
    workload = next(
        step for step in target["steps"] if step.get("if") == "matrix.role == 'workload'"
    )
    assert working_directory(workflow, target, workload, workspace, temporary) == (
        workspace / "target-job"
    )
    script = temporary / "generated-router.py"
    script.write_text(step["run"])
    monkeypatch.chdir(working_directory(workflow, router, step, workspace, temporary))
    runpy.run_path(str(script), run_name="__main__")
    assert output.read_text() == ("runs_on=" + json.dumps(selection, separators=(",", ":")) + "\n")
    assert calls == [
        "token",
        "reserve",
    ]
    assert target["defaults"] == {"run": {"working-directory": "./target-job"}}
