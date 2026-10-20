#!/usr/bin/env bash
# Prepare as the SSH user; the installed root-owned wrapper owns activation.
set -euo pipefail
release_id="${1:?Usage: deploy.sh RELEASE_ID [all|prepare|activate|activation-worker]}"
phase="${2:-all}"
[[ "$release_id" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$ ]] || exit 2
[[ "$phase" =~ ^(all|prepare|activate|activation-worker)$ ]] || exit 2
script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_directory/log.sh"
release="/opt/mutating/releases/$release_id"
[[ -f "$release/platform/web/dist/index.html" ]] || { echo "Missing CI-built web UI." >&2; exit 2; }
activation="$release/.activation"
if [[ "$phase" == all || "$phase" == prepare ]]; then
  export UV_PYTHON_INSTALL_DIR=/opt/mutating/python
  export UV_HTTP_TIMEOUT=60 UV_HTTP_RETRIES=2
  deploy_stage 'Install Python 3.12 (deadline 120s)' timeout --kill-after=10s 120s uv python install 3.12
  deploy_stage 'Python permissions (deadline 60s)' timeout --kill-after=5s 60s chmod -R a+rX /opt/mutating/python
  deploy_stage 'Install API dependencies (deadline 300s)' timeout --kill-after=10s 300s \
    uv sync --project "$release/platform/api" --frozen --no-dev --python 3.12
  deploy_stage 'Release permissions (deadline 60s)' timeout --kill-after=5s 60s chmod -R a+rX "$release"
fi
if [[ "$phase" == activation-worker ]]; then
  # Publish completion atomically even on failure; the observer never guesses
  # success from a disconnected SSH session or from an empty log.
  trap 'status=$?; printf "%s\n" "$status" > "$activation/status.tmp"; mv "$activation/status.tmp" "$activation/status"' EXIT
  deploy_stage 'Activate: lock, SQLite backup, migrations, startup and static publication' \
    sudo -n /usr/local/sbin/mutating-activate "$release_id"
  exit 0
fi
if [[ "$phase" == all || "$phase" == activate ]]; then
  if mkdir "$activation" 2>/dev/null; then
    nohup bash "$script_directory/deploy.sh" "$release_id" activation-worker \
      > "$activation/output.log" 2>&1 < /dev/null &
    printf '%s\n' "$!" > "$activation/pid"
  fi
  deploy_log "Activation log: $activation/output.log"
  deploy_log 'CI observes activation; disconnecting does not interrupt database maintenance.'
  # tail is only an observer. It is safe to stop it on timeout or cancellation.
  touch "$activation/output.log"
  tail -n +1 -f "$activation/output.log" & follower=$!
  trap 'kill "$follower" 2>/dev/null || true' EXIT
  while [[ ! -f "$activation/status" ]]; do sleep 1; done
  # Give tail one interval to flush the final output before collecting the status.
  sleep 1
  status="$(cat "$activation/status")"
  [[ "$status" =~ ^[0-9]+$ ]] || { echo '::error::Invalid activation status.' >&2; exit 2; }
  exit "$status"
fi
