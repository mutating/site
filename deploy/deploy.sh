#!/usr/bin/env bash
# Build the Python environment as the ordinary SSH deployment user.
set -euo pipefail
release_id="${1:?Usage: deploy.sh RELEASE_ID}"
[[ "$release_id" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$ ]] || exit 2
release="/opt/mutating/releases/$release_id"
[[ -f "$release/platform/web/dist/index.html" ]] || { echo "Missing CI-built web UI." >&2; exit 2; }
export UV_PYTHON_INSTALL_DIR=/opt/mutating/python
uv python install 3.12
chmod -R a+rX /opt/mutating/python
uv sync --project "$release/platform/api" --frozen --no-dev --python 3.12
chmod -R a+rX "$release"
sudo -n /usr/local/sbin/mutating-activate "$release_id"
