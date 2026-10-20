#!/usr/bin/env bash
# Shared stage output. Never print command arguments: they may contain credentials.
deploy_log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

deploy_heartbeat() {
  local stage="$1" started="$2" sleeper=
  trap '[[ -z "$sleeper" ]] || kill "$sleeper" 2>/dev/null || true; exit 0' TERM INT
  while true; do
    sleep 15 & sleeper=$!
    wait "$sleeper" || return 0
    deploy_log "$stage: still running ($((SECONDS - started))s)"
  done
}

deploy_stage() {
  local stage="$1" started=$SECONDS ticker status
  shift
  deploy_log "$stage: started"
  deploy_heartbeat "$stage" "$started" & ticker=$!
  if "$@"; then status=0; else status=$?; fi
  kill "$ticker" 2>/dev/null || true
  wait "$ticker" 2>/dev/null || true
  if (( status == 0 )); then
    deploy_log "$stage: completed ($((SECONDS - started))s)"
  else
    printf '::error::%s failed after %ss (exit %s).\n' "$stage" "$((SECONDS - started))" "$status" >&2
  fi
  return "$status"
}
