#!/usr/bin/env bash
set -euo pipefail
release_id="${1:?Usage: upload.sh RELEASE_ID [all|upload|prepare|activate]}"
phase="${2:-all}"
[[ "$release_id" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$ ]] || exit 2
[[ "$phase" =~ ^(all|upload|prepare|activate)$ ]] || exit 2
for setting in DEPLOY_HOST DEPLOY_PORT DEPLOY_USER SSHPASS; do
  [[ -n "${!setting:-}" ]] || { echo "::error::Missing deployment setting: $setting" >&2; exit 2; }
done
[[ "$DEPLOY_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || exit 2
[[ "$DEPLOY_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]] || exit 2
[[ "$DEPLOY_PORT" =~ ^[0-9]{1,5}$ ]] && (( 10#$DEPLOY_PORT >= 1 && 10#$DEPLOY_PORT <= 65535 )) || exit 2
export SSHPASS
script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$script_directory/.."
source "$script_directory/log.sh"
known_hosts="./deploy/known_hosts"
[[ -s "$known_hosts" ]] || { echo "::error::Missing pinned SSH host key: $known_hosts" >&2; exit 2; }
ssh_options=(-4 -p "$DEPLOY_PORT" -o PreferredAuthentications=password -o PubkeyAuthentication=no \
  -o NumberOfPasswordPrompts=1 -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes -o HostKeyAlias=mutating-production -o "UserKnownHostsFile=$known_hosts")
target="$DEPLOY_USER@$DEPLOY_HOST"
staging="/opt/mutating/releases/$release_id.uploading"
release="/opt/mutating/releases/$release_id"

if [[ "$phase" == all || "$phase" == upload ]]; then
  deploy_log 'SSH preflight: started (deadline 60s)'
  # Pin the basis to one release, even if /current changes during the transfer.
  if basis="$(timeout --kill-after=5s 60s sshpass -e ssh "${ssh_options[@]}" "$target" \
    "set -eu; test -w /opt/mutating/releases; command -v rsync >/dev/null; command -v uv >/dev/null; command -v timeout >/dev/null; test -x /usr/local/sbin/mutating-activate; test ! -e '$release'; mkdir -p '$staging'; readlink -f /opt/mutating/current || true")"; then
    deploy_log 'SSH preflight: completed'
  else
    status=$?; echo "::error::SSH preflight failed (exit $status)." >&2; exit "$status"
  fi
  basis_options=(--checksum)
  if [[ -n "$basis" ]]; then
    [[ "$basis" =~ ^/opt/mutating/releases/[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$ ]] || { echo '::error::Invalid current release path.' >&2; exit 2; }
    # Local copies keep releases independent; hard links would let preparation
    # alter permissions/content in the running release. Checksum ignores checkout mtimes.
    basis_options+=("--copy-dest=$basis")
    deploy_log "Upload: reusing unchanged files from $basis"
  fi
  printf -v rsync_shell '%q ' ssh "${ssh_options[@]}"
  # Upload code only; production credentials and data live outside the release tree.
  # The I/O timeout detects a stall; the outer deadline also catches a slow trickle.
  deploy_stage 'Upload (deadline 480s, idle timeout 60s)' \
    timeout --kill-after=10s 480s sshpass -e rsync \
    --archive --compress --delete-delay --delay-updates \
    --info=progress2 --outbuf=L --stats --timeout=60 "${basis_options[@]}" \
    --exclude='.git/' --exclude='.venv/' --exclude='venv/' --exclude='node_modules/' \
    --exclude='.env*' --exclude='data/' --exclude='.runtime/' --exclude='/dist/' \
    --exclude='__pycache__/' --exclude='.pytest_cache/' --exclude='.ruff_cache/' \
    --exclude='tests/' --exclude='test-results/' --exclude='playwright-report/' \
    --exclude='.coverage*' --exclude='htmlcov/' --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    -e "$rsync_shell" ./ "$target:$staging/"
  deploy_stage 'Finalize uploaded release (deadline 60s)' \
    timeout --kill-after=5s 60s sshpass -e ssh "${ssh_options[@]}" "$target" \
    "set -eu; test ! -e '$release'; mv '$staging' '$release'"
fi
if [[ "$phase" == all || "$phase" == prepare ]]; then
  deploy_stage 'Prepare Python environment (deadline 600s)' \
    timeout --kill-after=10s 600s sshpass -e ssh "${ssh_options[@]}" "$target" \
    "bash '$release/deploy/deploy.sh' '$release_id' prepare"
fi
if [[ "$phase" == all || "$phase" == activate ]]; then
  # Only the monitor is bounded here. A detached activation completes backup and
  # migration recovery even if CI is cancelled or the SSH connection disappears.
  if deploy_stage 'Monitor release activation (deadline 420s)' \
    timeout --kill-after=10s 420s sshpass -e ssh "${ssh_options[@]}" "$target" \
    "bash '$release/deploy/deploy.sh' '$release_id' activate"; then
    :
  else
    status=$?
    echo "::error::Inspect $release/.activation/output.log and status on the server before retrying. Activation may still be running; reconnect with the same release ID and phase activate." >&2
    exit "$status"
  fi
fi
