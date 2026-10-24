"""Workflow identity remains exact when a valid filename itself contains an @ sign."""

from types import SimpleNamespace

import pytest

from throng_api.ci_policy import workflow_run_allowed


@pytest.mark.parametrize(
    ("provider_path", "signed_path", "allowed", "expected"),
    [
        (".github/workflows/build@nightly.yml", ".github/workflows/build@nightly.yml", [], True),
        (
            ".github/workflows/build@nightly.yml@refs/heads/feature@ci",
            ".github/workflows/build@nightly.yml",
            [".github/workflows/build@nightly.yml"],
            True,
        ),
        (".github/workflows/build@nightly.yml", ".github/workflows/build.yml", [], False),
        (".github/workflows/build.yml.extra", ".github/workflows/build.yml", [], False),
        (
            ".github/workflows/build@nightly.yml",
            ".github/workflows/build@nightly.yml",
            [".github/workflows/deploy.yml"],
            False,
        ),
        (".github/workflows/build.yml@main", None, None, True),
        (".github/workflows/other.yml@main", None, None, False),
    ],
)
def test_provider_workflow_identity_uses_signed_filename_without_widening_scope(
    provider_path, signed_path, allowed, expected
):
    """An @ in a filename must survive verification without admitting other files or a removed scope."""
    route = SimpleNamespace(workflow_path=".github/workflows/build.yml", workflow_paths=allowed)
    assert workflow_run_allowed(route, provider_path, signed_path) is expected
