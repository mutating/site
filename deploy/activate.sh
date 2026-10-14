#!/usr/bin/env bash
# Root-owned activation wrapper, installed by Ansible. Application code runs as throng.
set -euo pipefail
release_id="${1:?Usage: mutating-activate RELEASE_ID}"
[[ "$release_id" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$ ]] || exit 2
deploy_root="/opt/mutating"
server_env="/etc/throng/throng.env"
release="$deploy_root/releases/$release_id"
[[ -d "$release" && -f "$release/site/public/index.html" && -r "$server_env" ]] || { echo "Missing release or server environment." >&2; exit 2; }
[[ "$(realpath "$release")" == "$release" ]] || { echo "Release must not be a symlink." >&2; exit 2; }
exec 9>"$deploy_root/deploy.lock"
flock --exclusive --wait 1200 9
was_running=false
if systemctl is-active --quiet throng.service; then was_running=true; fi
systemctl stop throng.service
backup_id="$release_id-$(date -u +%Y%m%dT%H%M%S)"
maintenance=(runuser -u throng -- "$release/platform/api/.venv/bin/python")
if ! "${maintenance[@]}" "$release/deploy/sqlite_backup.py" backup "$backup_id" --env-file "$server_env"; then
  echo "Backup failed; migrations were not started." >&2
  if $was_running; then systemctl start throng.service; fi
  exit 1
fi
if ! "${maintenance[@]}" "$release/deploy/run.py" migrate --env-file "$server_env"; then
  echo "Migration failed; restoring the pre-migration SQLite backup." >&2
  # All writers remain stopped, so restoring cannot lose newly accepted requests.
  "${maintenance[@]}" "$release/deploy/sqlite_backup.py" restore "$backup_id" --env-file "$server_env"
  if $was_running; then systemctl start throng.service; fi
  exit 1
fi

ln -sfn "$release" "$deploy_root/current.next"
mv -Tf "$deploy_root/current.next" "$deploy_root/current"
systemctl reset-failed throng.service
systemctl start throng.service
for attempt in $(seq 1 60); do
  if curl --fail --silent --max-time 2 http://127.0.0.1:8000/health/ready --output /dev/null \
    && curl --fail --silent --max-time 2 http://127.0.0.1:8001/account/login --output /dev/null; then
    rsync --archive --checksum --delete-delay --delay-updates --no-links \
      --chmod=D755,F644 "$release/site/public/" /var/www/mutating.tech/
    printf 'Deployed %s; SQLite backup: %s\n' "$release_id" "$backup_id"
    exit 0
  fi
  sleep 1
done
systemctl stop throng.service
journalctl --unit throng.service --no-pager --lines 100 >&2
echo "Startup failed. Release: $release; backup: $backup_id. Inspect before retrying systemctl start throng." >&2
# Preserve the migrated database: the new API may already have accepted writes.
exit 1
