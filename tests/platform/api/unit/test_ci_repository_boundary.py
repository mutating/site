"""Keep server CI independent of the separately maintained native application."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_ci_checks_out_only_this_repository(workflow):
    """A server PR must not need unpublished code from the native application."""
    data = yaml.safe_load(workflow.read_text())
    for job in data.get("jobs", {}).values():
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("actions/checkout@"):
                repository = step.get("with", {}).get("repository")
                assert repository in (None, "${{ github.repository }}", "mutating/site")
    assert "POWER_CELL_NETWORK_SERVER_ROOT" not in workflow.read_text()
