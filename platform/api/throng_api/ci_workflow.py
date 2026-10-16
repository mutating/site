"""Self-contained workflow example: embed the versioned standard-library router."""

from pathlib import Path
from textwrap import indent

# Packaged alongside the API so deployment does not depend on the checkout layout.
SOURCE = Path(__file__).with_name("ci_bootstrap.py").read_text()


def render(route, origin):
    script = indent(SOURCE.rstrip(), "          ")
    branch = route.ref.removeprefix("refs/heads/")
    return f'''# Merge the routing job and runs-on/needs into {route.workflow_path}.
# Linux ARM64 only. Fork/PR jobs use fallback; push/dispatch may use Power Cells.
name: Power Cells CI
on:
  push:
    branches: ["{branch}"]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  powercell_route:
    runs-on: ubuntu-24.04-arm
    timeout-minutes: 4
    permissions:
      id-token: write
    outputs:
      runs_on: ${{{{ steps.route.outputs.runs_on }}}}
      backend: ${{{{ steps.route.outputs.backend }}}}
    steps:
      - id: route
        name: Select CI runner
        shell: python
        env:
          POWERCELL_API: "{origin}"
          POWERCELL_AUDIENCE: "{origin}/v1/github/ci"
          POWERCELL_ROUTE: "{route.id}"
          POWERCELL_FALLBACK: "{route.fallback}"
          POWERCELL_WAIT_SECONDS: "{route.preparation_seconds}"
        run: |
{script}
  {route.job_key}:
    needs: powercell_route
    runs-on: ${{{{ fromJSON(needs.powercell_route.outputs.runs_on) }}}}
    timeout-minutes: {(route.timeout_seconds + 59) // 60}
    steps:
      - name: Verify runner
        run: uname -a
      # Add checkout and the existing CI steps here.
'''
