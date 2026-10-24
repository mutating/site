"""Self-contained workflow example: embed the versioned standard-library router."""

import hashlib
import json
from pathlib import Path
from textwrap import indent

from throng_api.ci_profiles import effective_profile

# Packaged alongside the API so deployment does not depend on the checkout layout.
SOURCE = Path(__file__).with_name("ci_bootstrap.py").read_text()


def render(route, origin, *, workflow_path=None, job_key=None):
    path, job = workflow_path or route.workflow_path, job_key or route.job_key
    modern = getattr(route, "workflow_paths", None) is not None or job_key is not None
    instance = hashlib.sha256((path + ":" + job).encode()).hexdigest()[:32]
    profile = effective_profile(route, path, ("preparation_seconds", "timeout_seconds"))
    script = indent(SOURCE.rstrip(), "          ")
    branch = literal_branch(route.ref.removeprefix("refs/heads/"))
    router_job = "powercell_route_" + str(route.id).replace("-", "")
    anchor = "powercell_bootstrap_" + str(route.id).replace("-", "")
    if modern:
        router_job += "_" + instance
        anchor += "_" + instance
    identity = f'          POWERCELL_INSTANCE: "{instance}"\n' if modern else ""
    rerun = "Re-run the routing job or all jobs to refresh Power Cell selection"
    selection = (
        f"needs.{router_job}.outputs.run_attempt == github.run_attempt "
        f"&& needs.{router_job}.outputs.runs_on || '{rerun}'"
    )
    return f'''# Merge the routing job and target matrix into {path}.
# Linux ARM64 only; push/dispatch on the bound branch.
# No available Mac: preparation fails and the dependent job is skipped.
# Require routing, workload and queue guard checks for branch protection.
# Rerun routing or all jobs.
name: Power Cells CI
on:
  push:
    branches: [{json.dumps(branch)}]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  {router_job}:
    # Copy the target job's upstream needs here too, e.g. needs: build.
    # Resolve approval/concurrency gates before routing; selection expires after 5 minutes.
    runs-on: ubuntu-24.04-arm
    timeout-minutes: {max(4, (profile["preparation_seconds"] + 59) // 60 + 1)}
    permissions:
      id-token: write
    outputs:
      runs_on: ${{{{ steps.route.outputs.runs_on }}}}
      run_attempt: ${{{{ github.run_attempt }}}}
    steps:
      - id: route
        name: Select CI runner
        shell: python
        working-directory: ${{{{ runner.temp }}}}
        env:
          POWERCELL_API: "{origin}"
          POWERCELL_AUDIENCE: "{origin}/v1/github/ci"
          POWERCELL_ROUTE: "{route.id}"
{identity.rstrip() if identity else ""}
          POWERCELL_WAIT_SECONDS: "{profile["preparation_seconds"]}"
        run: &{anchor} |
{script}
  {json.dumps(job)}:
    name: "{job} (${{{{ matrix.role == 'queue_guard' && 'queue guard' || 'workload' }}}})"
    needs: {router_job}
    strategy:
      fail-fast: true
      max-parallel: 2
      matrix:
        include:
          - role: workload
            runs_on: ${{{{ {selection} }}}}
            timeout_minutes: {(profile["timeout_seconds"] + 59) // 60}
          - role: queue_guard
            runs_on: '["ubuntu-24.04-arm"]'
            timeout_minutes: 6
    continue-on-error: false
    runs-on: ${{{{ fromJSON(matrix.runs_on) }}}}
    timeout-minutes: ${{{{ matrix.timeout_minutes }}}}
    permissions:
      contents: read
      id-token: write
    steps:
      - name: Watch Power Cell assignment
        if: matrix.role == 'queue_guard'
        shell: python
        working-directory: ${{{{ runner.temp }}}}
        env:
          POWERCELL_MODE: watch
          POWERCELL_API: "{origin}"
          POWERCELL_AUDIENCE: "{origin}/v1/github/ci"
          POWERCELL_ROUTE: "{route.id}"
{identity.rstrip() if identity else ""}
        run: *{anchor}
      # Every CI step MUST keep this workload condition: the guard only observes.
      - name: Verify runner
        if: matrix.role == 'workload'
        run: uname -a
      # Add checkout and the existing CI steps here, each with the workload condition.
'''


def literal_branch(branch):
    """A selected Git ref is a literal name rather than a GitHub branch filter pattern."""
    return "".join("\\" + char if char in "*?[]+!" else char for char in branch)
