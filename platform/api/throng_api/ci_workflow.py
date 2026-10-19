"""Self-contained workflow example: embed the versioned standard-library router."""

import json
from pathlib import Path
from textwrap import indent

# Packaged alongside the API so deployment does not depend on the checkout layout.
SOURCE = Path(__file__).with_name("ci_bootstrap.py").read_text()


def render(route, origin):
    script = indent(SOURCE.rstrip(), "          ")
    branch = route.ref.removeprefix("refs/heads/")
    router_job = "powercell_route_" + str(route.id).replace("-", "")
    anchor = "powercell_bootstrap_" + str(route.id).replace("-", "")
    rerun = "Re-run the routing job or all jobs to refresh Power Cell selection"
    selection = (
        f"needs.{router_job}.outputs.run_attempt == github.run_attempt "
        f"&& needs.{router_job}.outputs.runs_on || '{rerun}'"
    )
    return f'''# Merge the routing job and target matrix into {route.workflow_path}.
# Linux ARM64 only; push/dispatch on the bound branch.
# No available Mac: preparation fails and the dependent job is skipped.
# Require routing, workload and queue guard checks for branch protection.
# Rerun routing or all jobs.
name: Power Cells CI
on:
  push:
    branches: ["{branch}"]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  {router_job}:
    # Copy the target job's upstream needs here too, e.g. needs: build.
    # Resolve approval/concurrency gates before routing; selection expires after 5 minutes.
    runs-on: ubuntu-24.04-arm
    timeout-minutes: {max(4, (route.preparation_seconds + 59) // 60 + 1)}
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
          POWERCELL_WAIT_SECONDS: "{route.preparation_seconds}"
        run: &{anchor} |
{script}
  {json.dumps(route.job_key)}:
    name: "{route.job_key} (${{{{ matrix.role == 'queue_guard' && 'queue guard' || 'workload' }}}})"
    needs: {router_job}
    strategy:
      fail-fast: true
      max-parallel: 2
      matrix:
        include:
          - role: workload
            runs_on: ${{{{ {selection} }}}}
            timeout_minutes: {(route.timeout_seconds + 59) // 60}
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
        run: *{anchor}
      # Every CI step MUST keep this workload condition: the guard only observes.
      - name: Verify runner
        if: matrix.role == 'workload'
        run: uname -a
      # Add checkout and the existing CI steps here, each with the workload condition.
'''
