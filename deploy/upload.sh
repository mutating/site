#!/usr/bin/env bash
set -euo pipefail
release_id="${1:?Usage: upload.sh RELEASE_ID}"
[[ "$release_id" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$ ]] || exit 2
for setting in DEPLOY_HOST DEPLOY_PORT DEPLOY_USER SSHPASS; do
  [[ -n "${!setting:-}" ]] || { echo "::error::Missing deployment setting: $setting" >&2; exit 2; }
done
[[ "$DEPLOY_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || exit 2
[[ "$DEPLOY_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]] || exit 2
[[ "$DEPLOY_PORT" =~ ^[0-9]{1,5}$ ]] && (( 10#$DEPLOY_PORT >= 1 && 10#$DEPLOY_PORT <= 65535 )) || exit 2
export SSHPASS
script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$script_directory/.."
known_hosts="./deploy/known_hosts"
[[ -s "$known_hosts" ]] || { echo "::error::Missing pinned SSH host key: $known_hosts" >&2; exit 2; }
ssh_options=(-4 -p "$DEPLOY_PORT" -o PreferredAuthentications=password -o PubkeyAuthentication=no \
  -o NumberOfPasswordPrompts=1 -o ConnectTimeout=15 -o StrictHostKeyChecking=yes \
  -o HostKeyAlias=mutating-production -o "UserKnownHostsFile=$known_hosts")
target="$DEPLOY_USER@$DEPLOY_HOST"
staging="/opt/mutating/releases/$release_id.uploading"
release="/opt/mutating/releases/$release_id"

sshpass -e ssh "${ssh_options[@]}" "$target" \
  "set -eu; test -w /opt/mutating/releases; command -v rsync; command -v uv; test -x /usr/local/sbin/mutating-activate; test ! -e '$release'; mkdir -p '$staging'"
# Upload code only; production credentials and data live outside the release tree.
rsync_shell="ssh -4 -p $DEPLOY_PORT -o PreferredAuthentications=password -o PubkeyAuthentication=no -o NumberOfPasswordPrompts=1 -o ConnectTimeout=15 -o StrictHostKeyChecking=yes -o HostKeyAlias=mutating-production -o UserKnownHostsFile=$known_hosts"
sshpass -e rsync --archive --compress --delete-delay --delay-updates \
  --exclude='.git/' --exclude='.venv/' --exclude='venv/' --exclude='node_modules/' \
  --exclude='.env*' --exclude='data/' --exclude='.runtime/' --exclude='/dist/' \
  --exclude='__pycache__/' --exclude='.pytest_cache/' --exclude='.ruff_cache/' \
  --exclude='tests/' --exclude='test-results/' --exclude='playwright-report/' \
  --exclude='.coverage*' --exclude='htmlcov/' --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
  -e "$rsync_shell" ./ "$target:$staging/"
sshpass -e ssh "${ssh_options[@]}" "$target" \
  "set -eu; mv '$staging' '$release'; bash '$release/deploy/deploy.sh' '$release_id'"
