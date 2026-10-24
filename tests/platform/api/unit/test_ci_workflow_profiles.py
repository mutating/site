"""Generated YAML retains its identity and effective profile without mutating policies."""

import hashlib
from types import SimpleNamespace

import pytest
import yaml

from throng_api.ci_workflow import render


@pytest.mark.parametrize("mode", ["absent", "legacy", "all", "narrow"])
@pytest.mark.parametrize("explicit_path", [False, True])
@pytest.mark.parametrize("job", [None, "build_112857", "build_" + "x" * 506])
def test_yaml_uses_selected_profile_and_identity_without_changing_the_route(
    mode, explicit_path, job
):
    """Legacy/default and explicit instances must keep separate identities and exact profile timeouts, including long jobs and literal branch filters."""
    default, deploy = ".github/workflows/ci.yml", ".github/workflows/deploy.yml"
    route = SimpleNamespace(
        id="01234567-89ab-cdef-0123-456789abcdef",
        ref="refs/heads/release/!v+1",
        workflow_path=default,
        job_key="test",
        preparation_seconds=90,
        timeout_seconds=3600,
        workflow_profiles={deploy: {"preparation_seconds": 300, "timeout_seconds": 61}},
    )
    if mode != "absent":
        route.workflow_paths = {"legacy": None, "all": [], "narrow": [deploy]}[mode]
    before = vars(route).copy()
    path, target = (deploy if explicit_path else default), job or "test"
    text = render(
        route, "https://api.example", workflow_path=deploy if explicit_path else None, job_key=job
    )
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    assert text.startswith(f"# Merge the routing job and target matrix into {path}.\n")
    assert workflow["on"]["push"]["branches"] == [r"release/\!v\+1"]
    instance = hashlib.sha256(f"{path}:{target}".encode()).hexdigest()[:32]
    modern = mode in {"all", "narrow"} or job is not None
    router_id = "powercell_route_" + route.id.replace("-", "") + ("_" + instance if modern else "")
    assert set(workflow["jobs"]) == {router_id, target}
    router, workload = workflow["jobs"][router_id], workflow["jobs"][target]
    assert workload["needs"] == router_id
    assert router["timeout-minutes"] == ("6" if explicit_path else "4")
    env = router["steps"][0]["env"]
    assert env["POWERCELL_ROUTE"] == route.id
    assert env["POWERCELL_WAIT_SECONDS"] == ("300" if explicit_path else "90")
    assert env.get("POWERCELL_INSTANCE") == (instance if modern else None)
    assert workload["steps"][0]["env"].get("POWERCELL_INSTANCE") == (instance if modern else None)
    assert workload["strategy"]["matrix"]["include"][0]["timeout_minutes"] == (
        "2" if explicit_path else "60"
    )
    assert vars(route) == before
