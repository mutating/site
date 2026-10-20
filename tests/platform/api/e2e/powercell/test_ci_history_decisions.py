"""History distinguishes preparation failures from exact executed-job results."""

import pytest

from .test_ci import ci_peer as ci_peer
from .test_ci import github as github
from .test_ci import powercell as powercell
from .test_ci_chain import chain as chain

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("settings", [{"controller_enabled": False}], indirect=True),
]


# Assertions: lack of capacity, image/registration errors and an API outage
# reach the real generated bootstrap. History must agree with its outcome: failed
# every refusal fails without runner output; cleanup leaves no runner.
@pytest.mark.parametrize("failure", ["no_capacity", "image", "github", "api"])
async def test_history_agrees_with_the_effective_routing_decision(chain, powercell, failure):
    """History records the actual preparation failure and its cleanup."""
    await chain.bind()
    if failure == "no_capacity":
        response = await chain.api.patch(
            f"/v1/computers/{powercell.computer_id}", json={"enabled": False}
        )
        assert response.status_code == 200
    else:
        chain.agent = powercell
        chain.create_failure = failure == "image"
    if failure == "github":
        chain.peer.failures[("POST", "/repos/test-org/repo/actions/runners/generate-jitconfig")] = (
            503
        )
    chain.offline_api = failure == "api"
    with pytest.raises(RuntimeError):
        await chain.bootstrap()
    assert not chain.outputs[0].exists() and chain.job is None
    chain.peer.failures.clear()
    await chain.drain()
    response = await chain.api.get(f"/v1/github/routes/{chain.route['id']}/runs")
    assert response.status_code == 200
    [run] = response.json()
    assert run["status"] == ("cancelled" if failure == "api" else "failed")
    assert run["reason"] == (await chain.state()).reason
    assert run["url"].endswith("/actions/runs/1234/attempts/1")
    assert not chain.peer.registered and (await chain.state()).cleaned_at


# Assertions: Power Cell selection preserves exact executed-job results. Success,
# job failure and cancellation keep their exact provider outcome in history;
# the policy never reinterprets a job which actually ran as a routing failure.
@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled"])
async def test_powercell_job_preserves_its_exact_result(chain, powercell, conclusion):
    """Keep exact executed-job results distinct from failure to select any runner."""
    await chain.bind()
    chain.agent = powercell
    outputs = await chain.bootstrap()
    assert outputs["runs_on"].startswith('["powercell-')
    await chain.start_job(outputs)
    await chain.finish_job(conclusion)
    task = next(task for task in chain.tasks if task["kind"] == "run")
    response = await powercell.client.post(
        f"/agent/v1/tasks/{task['id']}/complete", json={"returncode": 0}
    )
    assert response.status_code == 200
    await chain.drain()
    response = await chain.api.get(f"/v1/github/routes/{chain.route['id']}/runs")
    assert response.status_code == 200
    [run] = response.json()
    assert run["status"] == ("cancelled" if conclusion == "cancelled" else "completed")
    assert run["reason"] == "github_" + conclusion
