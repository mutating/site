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
  # Root-run maintenance may have left bytecode caches in this shared Python.
  # They are disposable, and the deployment user must not chmod another user's files.
  deploy_stage 'Python permissions (deadline 60s)' timeout --kill-after=5s 60s \
    find /opt/mutating/python -name __pycache__ -prune -o ! -type l -user "$(id -un)" -exec chmod a+rX '{}' +
  deploy_stage 'Install API dependencies (deadline 300s)' timeout --kill-after=10s 300s \
    uv sync --project "$release/platform/api" --frozen --no-dev --python 3.12
  deploy_stage 'Release permissions (deadline 60s)' timeout --kill-after=5s 60s \
    find "$release" -name __pycache__ -prune -o ! -type l -user "$(id -un)" -exec chmod a+rX '{}' +
fi
if [[ "$phase" == activation-worker ]]; then
  # Publish completion atomically even on failure; the observer never guesses
  # success from a disconnected SSH session or from an empty log.
  finish_activation() {
    local status=$?
    trap - EXIT
    if ! { printf '%s\n' "$status" > "$activation/status.tmp" && mv "$activation/status.tmp" "$activation/status"; }; then
      echo 'Could not publish activation status; inspect disk space and the activation log.' >&2
    fi
    exit "$status"
  }
  trap finish_activation EXIT
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
  missing_pid=0
  while [[ ! -f "$activation/status" ]]; do
    worker_pid="$(cat "$activation/pid" 2>/dev/null || true)"
    if [[ ! "$worker_pid" =~ ^[1-9][0-9]*$ ]]; then
      # Another observer can arrive between mkdir and publishing the PID.
      missing_pid=$((missing_pid + 1))
      if (( missing_pid < 5 )); then sleep 1; continue; fi
      echo '::error::Activation PID is missing or invalid; inspect the activation log before retrying.' >&2
      exit 1
    fi
    worker_state="$(ps -o stat= -p "$worker_pid" 2>/dev/null || true)"
    worker_state="${worker_state//[[:space:]]/}"
    if [[ -z "$worker_state" || "$worker_state" == Z* ]]; then
      # Recheck after observing process death: its EXIT trap may just have published.
      [[ ! -f "$activation/status" ]] || break
      echo '::error::Activation worker exited without a final status; check disk space and the activation log. Activation was not confirmed successful.' >&2
      exit 1
    fi
    sleep 1
  done
  # Give tail one interval to flush the final output before collecting the status.
  sleep 1
  status="$(cat "$activation/status")"
  [[ "$status" =~ ^[0-9]+$ ]] || { echo '::error::Invalid activation status.' >&2; exit 2; }
  exit "$status"
fi
