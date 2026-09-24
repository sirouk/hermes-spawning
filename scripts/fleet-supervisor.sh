#!/bin/sh
# fleet supervisor: keep every running hermes instance's webui + serve routes alive.
# Spawn flow installs the checkout + initial launch; this loop heals restarts/drift.
set -u
REPO="${HERMES_SPAWNING_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
INTERVAL="${FLEET_SUPERVISOR_INTERVAL:-30}"
LOG="${FLEET_SUPERVISOR_LOG:-$REPO/instances/.supervisor.log}"
# Webui origin port for the Hermex iOS client. Single source of truth is
# stack-defaults.yaml (webui.serve_port); parsed with sed so this stays
# dependency-free, with the shipped default as the fallback.
WEBUI_SERVE_PORT="$(sed -n 's/^[[:space:]]*serve_port:[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
  "$REPO/stack-defaults.yaml" 2>/dev/null | head -1)"
[ -n "$WEBUI_SERVE_PORT" ] || WEBUI_SERVE_PORT=8788
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
logln() { printf '%s %s\n' "$(ts)" "$*" >> "$LOG"; }

ensure_instance() {
  local dir="$1" name basename_home base_path control launch home_url
  basename_home="$(basename "$dir")"
  control="$dir/control.env"
  [ -f "$control" ] || return 0
  name="$(awk -F= '/^TAILSCALE_HOSTNAME=/{print $2; exit}' "$control")"
  [ -n "$name" ] || name="$(basename "$dir")"
  # only doctor instances whose containers are actually supposed to be up
  running="$(docker compose --env-file "$control" -f "$REPO/compose.yaml" -p "hermes-$name" ps --status running --services 2>/dev/null | grep -cx hermes || true)"
  if [ "$running" != "1" ]; then
    return 0
  fi
  # Only exit 1 means config drift. Errors must never trigger mutation.
  stack_rc=0
  "$REPO/hermes-spawn.sh" stack-status "$name" >/dev/null 2>&1 || stack_rc=$?
  case "$stack_rc" in
    0) : ;;
    1)
      logln "$name: model-stack drift — applying fleet defaults"
      if "$REPO/hermes-spawn.sh" apply-stack "$name" -y --restart >/dev/null 2>&1; then
        if "$REPO/hermes-spawn.sh" stack-status "$name" >/dev/null 2>&1; then
          logln "$name: stack apply OK (verified)"
        else
          logln "$name: stack apply verification FAILED"
        fi
      else
        logln "$name: stack apply FAILED"
      fi ;;
    *) logln "$name: stack-status operational error (exit $stack_rc); not applying" ;;
  esac
  # webui daemon alive? (ctl.sh status exits 0 even when stopped — parse the
  # text, never the rc; ctl.sh is its own supervisor so re-start is safe)
  if [ -f "$dir/hermes-data/hermes-webui/ctl.sh" ]; then
    st="$(docker compose --env-file "$control" -f "$REPO/compose.yaml" -p "hermes-$name" \
      exec -T -u 10000 -w /opt/data/hermes-webui hermes bash ctl.sh status 2>/dev/null || true)"
    case "$st" in *"— running"*|*"- running"*) : ;;
      *)
        logln "$name: webui down — restarting"
        docker compose --env-file "$control" -f "$REPO/compose.yaml" -p "hermes-$name" \
          exec -T -u 10000 -w /opt/data/hermes-webui hermes bash ctl.sh start >/dev/null 2>&1 \
          && logln "$name: webui restart OK" || logln "$name: webui restart FAILED" ;;
    esac
  fi
  # serve routes present? (add-only)
  routes="$(docker compose --env-file "$control" -f "$REPO/compose.yaml" -p "hermes-$name" \
    exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve status 2>/dev/null || true)"
  case "$routes" in *9119*) : ;;
    *) logln "$name: root route missing — adding"
       docker compose --env-file "$control" -f "$REPO/compose.yaml" -p "hermes-$name" exec -T tailscale \
         tailscale --socket=/tmp/tailscaled.sock serve --bg --set-path / http://127.0.0.1:9119 >/dev/null 2>&1 \
         && logln "$name: root route re-added" || logln "$name: root route add FAILED" ;;
  esac
  case "$routes" in *"/webui"*8787*) : ;;
    *) logln "$name: /webui route missing — adding"
       docker compose --env-file "$control" -f "$REPO/compose.yaml" -p "hermes-$name" exec -T tailscale \
         tailscale --socket=/tmp/tailscaled.sock serve --bg --set-path /webui http://127.0.0.1:8787 >/dev/null 2>&1 \
         && logln "$name: /webui route re-added" || logln "$name: /webui route add FAILED" ;;
  esac
  # Hermex (iOS) origin route: the app drops the URL path, so the webui must also
  # answer at the root of its own HTTPS port. Match the ":8788" header line that
  # 'serve status' prints for that port, not a bare "8788" anywhere in the text.
  case "$routes" in *":$WEBUI_SERVE_PORT"*) : ;;
    *) logln "$name: webui origin route (:$WEBUI_SERVE_PORT) missing — adding"
       docker compose --env-file "$control" -f "$REPO/compose.yaml" -p "hermes-$name" exec -T tailscale \
         tailscale --socket=/tmp/tailscaled.sock serve --bg --https="$WEBUI_SERVE_PORT" http://127.0.0.1:8787 >/dev/null 2>&1 \
         && logln "$name: webui origin route re-added" || logln "$name: webui origin route add FAILED" ;;
  esac
}

logln "supervisor up (repo=$REPO interval=${INTERVAL}s)"
while :; do
  for d in "$REPO"/instances/*/; do
    [ -d "$d" ] || continue
    ensure_instance "$d" 2>/dev/null || true
  done
  sleep "$INTERVAL"
done
