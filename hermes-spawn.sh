#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly COMPOSE_FILE="$SCRIPT_DIR/compose.yaml"
readonly INSTANCES_DIR="$SCRIPT_DIR/instances"
readonly SHARED_TAILSCALE_AUTHKEY_FILE="$SCRIPT_DIR/.tailscale-authkey"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Hermes Spawning — isolated Hermes Agent + Tailscale environments

Usage:
  ./hermes-spawn.sh spawn                 Create and launch an instance
  ./hermes-spawn.sh list                  List instances
  ./hermes-spawn.sh start [instance]      Start an existing instance
  ./hermes-spawn.sh stop [instance]       Stop it (persistent data remains)
  ./hermes-spawn.sh restart [instance]    Restart it
  ./hermes-spawn.sh status [instance]     Show containers and Tailnet status
  ./hermes-spawn.sh logs [instance]       Follow both containers' logs
  ./hermes-spawn.sh setup [instance]      Run Hermes' setup wizard
  ./hermes-spawn.sh config [instance]     Alias for setup
  ./hermes-spawn.sh chat [instance]       Open the selected Hermes profile
  ./hermes-spawn.sh shell [instance]      Open a shell inside Hermes
  ./hermes-spawn.sh doctor [instance]     Run Hermes diagnostics
  ./hermes-spawn.sh serve [instance]      Configure private Tailnet HTTPS
  ./hermes-spawn.sh reauth [instance]     Replace an expired Tailscale login
  ./hermes-spawn.sh credentials [instance] Show URLs, API key, and web login
  ./hermes-spawn.sh update [instance]     Pull images and recreate containers
  ./hermes-spawn.sh apply-stack [instance]  Apply the fleet model-stack defaults
  ./hermes-spawn.sh stack-status [instance] Audit the defaults; exit 1 on drift
  ./hermes-spawn.sh webui-ensure [instance]  Deploy+launch the instance webui chat UI
  ./hermes-spawn.sh webui-status [instance]  Show webui status
  ./hermes-spawn.sh supervisor install|enable|disable|status
                                     Fleet webui/routes autostart (systemd)
  ./hermes-spawn.sh info [instance]       Show the collected host snapshot
  ./hermes-spawn.sh preflight             Check host requirements only

Stack defaults live in stack-defaults.yaml. Re-run apply-stack after adding
personas (profiles) to an instance. Run ./deploy.sh first to bring up the
host model stack (CCS + CLIProxy + OAuth + Chutes) that endpoints point at.

An instance name is its Tailscale hostname. If omitted, the script selects the
only instance or prompts when several exist.
USAGE
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

validate_hostname() {
  [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]
}

validate_profile() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$ ]] || return 1
  case "$1" in
    hermes|test|tmp|root|sudo) return 1 ;;
  esac
}

validate_cpu() {
  [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk -v n="$1" 'BEGIN { exit !(n > 0) }'
}

validate_memory() {
  [[ "$1" =~ ^[1-9][0-9]*([kmgtKMGT]|[kmgtKMGT][bB])$ ]]
}

prompt_default() {
  local prompt="$1" default="$2" value
  read -r -p "$prompt [$default]: " value
  printf '%s' "${value:-$default}"
}

prompt_secret() {
  local prompt="$1" value
  read -r -s -p "$prompt" value
  printf '\n' >&2
  printf '%s' "$value"
}

tailscale_authkey() {
  local prompt="$1" value mode
  if [[ -s "$SHARED_TAILSCALE_AUTHKEY_FILE" ]]; then
    mode="$(stat -c '%a' "$SHARED_TAILSCALE_AUTHKEY_FILE" 2>/dev/null || true)"
    [[ "$mode" == 600 ]] || die "$SHARED_TAILSCALE_AUTHKEY_FILE must have mode 0600."
    value="$(<"$SHARED_TAILSCALE_AUTHKEY_FILE")"
    [[ "$value" != *$'\n'* && "$value" == tskey-* ]] || die "Invalid reusable key in $SHARED_TAILSCALE_AUTHKEY_FILE"
    info "Using the reusable Tailscale auth key from $SHARED_TAILSCALE_AUTHKEY_FILE" >&2
  else
    value="$(prompt_secret "$prompt")"
  fi
  [[ "$value" == tskey-* ]] || die "A Tailscale auth key (tskey-...) is required."
  printf '%s' "$value"
}

confirm() {
  local prompt="$1" default="${2:-yes}" answer suffix
  if [[ "$default" == yes ]]; then suffix='[Y/n]'; else suffix='[y/N]'; fi
  read -r -p "$prompt $suffix " answer
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]
}

host_cpu_count() {
  getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || printf '4\n'
}

host_memory_mib() {
  awk '/^MemTotal:/ { print int($2 / 1024); exit }' /proc/meminfo 2>/dev/null || printf '8192\n'
}

default_cpu_limit() {
  local total="$1"
  if (( total >= 8 )); then printf '%s.0\n' "$((total - 1))"
  elif (( total >= 2 )); then printf '%s.0\n' "$total"
  else printf '1.0\n'; fi
}

default_memory_limit() {
  local total_mib="$1" gib
  gib=$((total_mib * 80 / 100 / 1024))
  (( gib < 2 )) && gib=2
  printf '%sg\n' "$gib"
}

preflight() {
  require_command docker
  require_command awk
  require_command flock
  require_command openssl
  [[ -f "$COMPOSE_FILE" ]] || die "Missing $COMPOSE_FILE"
  docker info >/dev/null 2>&1 || die "Docker daemon is not reachable by this user."
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required."
  [[ -c /dev/net/tun ]] || die "/dev/net/tun is missing; load the tun module before spawning."
  info "Docker, Compose, and /dev/net/tun are ready."
}

instance_dirs() {
  local path
  shopt -s nullglob
  for path in "$INSTANCES_DIR"/*/control.env; do dirname -- "$path"; done
  shopt -u nullglob
}

list_instances() {
  local dir any=0 state profile
  printf '%-28s %-20s %-12s\n' HOSTNAME PROFILE STATE
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    any=1
    COMPOSE_PROJECT_NAME="$(read_control_value COMPOSE_PROJECT_NAME "$dir/control.env")"
    state="stopped"
    if docker ps --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null | grep -Fqx "$COMPOSE_PROJECT_NAME"; then
      state="running"
    fi
    profile="$(read_control_value HERMES_PROFILE "$dir/control.env")"
    profile="${profile:-unknown}"
    printf '%-28s %-20s %-12s\n' "$(basename "$dir")" "$profile" "$state"
  done < <(instance_dirs)
  (( any == 1 )) || printf '%s\n' '(no instances yet)'
}

read_control_value() {
  local key="$1" file="$2"
  awk -v wanted="$key" '
    index($0, wanted "=") == 1 {
      print substr($0, length(wanted) + 2)
      exit
    }
  ' "$file"
}

resolve_instance() {
  local requested="${1:-}" dir count=0 only="" choice
  if [[ -n "$requested" ]]; then
    validate_hostname "$requested" || die "Invalid instance name: $requested"
    dir="$INSTANCES_DIR/$requested"
    [[ -f "$dir/control.env" ]] || die "Unknown instance: $requested"
    printf '%s\n' "$dir"
    return
  fi
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    only="$dir"
    count=$((count + 1))
  done < <(instance_dirs)
  (( count > 0 )) || die "No instances exist. Run: ./hermes-spawn.sh spawn"
  if (( count == 1 )); then printf '%s\n' "$only"; return; fi
  list_instances >&2
  read -r -p 'Instance hostname: ' choice
  validate_hostname "$choice" || die "Invalid instance name: $choice"
  dir="$INSTANCES_DIR/$choice"
  [[ -f "$dir/control.env" ]] || die "Unknown instance: $choice"
  printf '%s\n' "$dir"
}

load_instance() {
  local dir="$1"
  local control="$dir/control.env" key value
  local keys=(
    COMPOSE_PROJECT_NAME TAILSCALE_HOSTNAME TAILSCALE_STATE_DIR
    TAILSCALE_AUTHKEY_FILE TS_AUTHKEY_SPEC HERMES_DATA_DIR HERMES_PROFILE
    HERMES_UID HERMES_GID HERMES_CPUS HERMES_MEMORY
    HERMES_MEMORY_RESERVATION HERMES_SHM_SIZE HERMES_PIDS_LIMIT
    HERMES_IMAGE TAILSCALE_IMAGE
    MODEL_STACK_BASE_URL MODEL_STACK_API_KEY
  )
  for key in "${keys[@]}"; do
    value="$(read_control_value "$key" "$control")"
    printf -v "$key" '%s' "$value"
  done
  validate_hostname "$TAILSCALE_HOSTNAME" || die "Unsafe hostname in $control"
  validate_profile "$HERMES_PROFILE" || die "Unsafe profile in $control"
  [[ "$COMPOSE_PROJECT_NAME" == "hermes-$TAILSCALE_HOSTNAME" ]] || die "Unsafe project name in $control"
  [[ "$TAILSCALE_STATE_DIR" == "$dir/tailscale-state" ]] || die "Unexpected Tailscale state path in $control"
  [[ "$TAILSCALE_AUTHKEY_FILE" == "$dir/tailscale-authkey" ]] || die "Unexpected auth-key path in $control"
  [[ "$TS_AUTHKEY_SPEC" == file:/run/secrets/tailscale-authkey ]] || die "Unsafe auth-key setting in $control"
  [[ "$HERMES_DATA_DIR" == "$dir/hermes-data" ]] || die "Unexpected Hermes data path in $control"
  [[ "$HERMES_UID" =~ ^[1-9][0-9]*$ && "$HERMES_GID" =~ ^[1-9][0-9]*$ ]] || die "Unsafe UID/GID in $control"
  validate_cpu "$HERMES_CPUS" || die "Unsafe CPU limit in $control"
  validate_memory "$HERMES_MEMORY" || die "Unsafe memory limit in $control"
  validate_memory "$HERMES_MEMORY_RESERVATION" || die "Unsafe memory reservation in $control"
  validate_memory "$HERMES_SHM_SIZE" || die "Unsafe shared-memory size in $control"
  [[ "$HERMES_PIDS_LIMIT" =~ ^[1-9][0-9]*$ ]] || die "Unsafe PID limit in $control"
  [[ -n "$HERMES_IMAGE" && -n "$TAILSCALE_IMAGE" ]] || die "Missing image setting in $control"
  [[ -z "$MODEL_STACK_BASE_URL" || "$MODEL_STACK_BASE_URL" =~ ^https?://[A-Za-z0-9.:-]+(/v1)?$ ]] || die "Unsafe MODEL_STACK_BASE_URL in $control"
  [[ -z "$MODEL_STACK_API_KEY" || ( "$MODEL_STACK_API_KEY" =~ ^[A-Za-z0-9._~+:/=-]{1,220}$ ) ]] || die "Unsafe MODEL_STACK_API_KEY in $control"
  export COMPOSE_PROJECT_NAME TAILSCALE_HOSTNAME TAILSCALE_STATE_DIR
  export TAILSCALE_AUTHKEY_FILE TS_AUTHKEY_SPEC HERMES_DATA_DIR HERMES_PROFILE
  export HERMES_UID HERMES_GID HERMES_CPUS HERMES_MEMORY
  export HERMES_MEMORY_RESERVATION HERMES_SHM_SIZE HERMES_PIDS_LIMIT
  export HERMES_IMAGE TAILSCALE_IMAGE
  export MODEL_STACK_BASE_URL MODEL_STACK_API_KEY
}

compose() {
  docker compose --env-file "$INSTANCE_DIR/control.env" -f "$COMPOSE_FILE" "$@"
}

# One-shot CLI commands must bypass the image's service supervisor. Recent
# Hermes images otherwise also start the dashboard, which can keep
# `docker compose run` alive after the requested command has completed.
hermes_run() {
  compose run --rm --no-deps --entrypoint hermes hermes "$@"
}

collect_host_info() {
  {
    printf 'collected_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'host=%s\n' "$(hostname -f 2>/dev/null || hostname)"
    printf 'kernel=%s\n' "$(uname -srmo)"
    printf 'architecture=%s\n' "$(uname -m)"
    printf 'cpu_count=%s\n' "$(host_cpu_count)"
    printf 'memory_mib=%s\n' "$(host_memory_mib)"
    printf 'workspace_filesystem='; df -hP "$SCRIPT_DIR" | awk 'NR==2 {print $2 " total, " $4 " available, " $6 " mount"}'
    printf 'docker='; docker version --format 'client={{.Client.Version}} server={{.Server.Version}}' 2>/dev/null || printf 'unavailable\n'
    printf 'compose='; docker compose version --short 2>/dev/null || printf 'unavailable\n'
    printf 'docker_security='; docker info --format '{{join .SecurityOptions ","}}' 2>/dev/null || printf 'unknown\n'
    if command -v nvidia-smi >/dev/null 2>&1; then
      printf 'gpu='; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | paste -sd ';' - || true
    else
      printf 'gpu=not-detected\n'
    fi
  } > "$1"
  chmod 600 "$1"
}

write_control_file() {
  local target="$1"
  {
    printf 'COMPOSE_PROJECT_NAME=%s\n' "$COMPOSE_PROJECT_NAME"
    printf 'TAILSCALE_HOSTNAME=%s\n' "$TAILSCALE_HOSTNAME"
    printf 'TAILSCALE_STATE_DIR=%s\n' "$TAILSCALE_STATE_DIR"
    printf 'TAILSCALE_AUTHKEY_FILE=%s\n' "$TAILSCALE_AUTHKEY_FILE"
    printf 'TS_AUTHKEY_SPEC=%s\n' "$TS_AUTHKEY_SPEC"
    printf 'HERMES_DATA_DIR=%s\n' "$HERMES_DATA_DIR"
    printf 'HERMES_PROFILE=%s\n' "$HERMES_PROFILE"
    printf 'HERMES_UID=%s\n' "$HERMES_UID"
    printf 'HERMES_GID=%s\n' "$HERMES_GID"
    printf 'HERMES_CPUS=%s\n' "$HERMES_CPUS"
    printf 'HERMES_MEMORY=%s\n' "$HERMES_MEMORY"
    printf 'HERMES_MEMORY_RESERVATION=%s\n' "$HERMES_MEMORY_RESERVATION"
    printf 'HERMES_SHM_SIZE=%s\n' "$HERMES_SHM_SIZE"
    printf 'HERMES_PIDS_LIMIT=%s\n' "$HERMES_PIDS_LIMIT"
    printf 'HERMES_IMAGE=%s\n' "$HERMES_IMAGE"
    printf 'TAILSCALE_IMAGE=%s\n' "$TAILSCALE_IMAGE"
    printf 'MODEL_STACK_BASE_URL=%s\n' "${MODEL_STACK_BASE_URL:-}"
    printf 'MODEL_STACK_API_KEY=%s\n' "${MODEL_STACK_API_KEY:-}"
  } > "$target"
  chmod 600 "$target"
}

wait_for_tailscale() {
  local attempt state
  info "Waiting for $TAILSCALE_HOSTNAME to join the tailnet..."
  for attempt in {1..60}; do
    state="$(compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock status --json 2>/dev/null || true)"
    if printf '%s\n' "$state" | grep -Eq '"BackendState"[[:space:]]*:[[:space:]]*"Running"'; then
      info "Tailscale is connected."
      : > "$TAILSCALE_AUTHKEY_FILE"
      chmod 600 "$TAILSCALE_AUTHKEY_FILE"
      return 0
    fi
    sleep 2
  done
  compose logs --tail=50 tailscale >&2 || true
  die "Tailscale did not become ready within two minutes. Fix authentication, then run start."
}

profile_env_path() {
  if [[ "$HERMES_PROFILE" == default ]]; then
    printf '%s/.env\n' "$HERMES_DATA_DIR"
  else
    printf '%s/profiles/%s/.env\n' "$HERMES_DATA_DIR" "$HERMES_PROFILE"
  fi
}

initialize_profile() {
  local api_key="$1" profile_env temp_env
  info "Preparing Hermes profile '$HERMES_PROFILE'..."
  if [[ "$HERMES_PROFILE" != default ]]; then
    hermes_run profile create "$HERMES_PROFILE"
  fi
  hermes_run profile use "$HERMES_PROFILE"
  profile_env="$(profile_env_path)"
  mkdir -p -- "$(dirname -- "$profile_env")"
  temp_env="$(mktemp "${profile_env}.tmp.XXXXXX")"
  if [[ -f "$profile_env" ]]; then
    awk '
      !/^(API_SERVER_ENABLED|API_SERVER_HOST|API_SERVER_PORT|API_SERVER_KEY|API_SERVER_CORS_ORIGINS)=/
    ' "$profile_env" > "$temp_env"
  fi
  {
    printf 'API_SERVER_ENABLED=true\n'
    printf 'API_SERVER_HOST=127.0.0.1\n'
    printf 'API_SERVER_PORT=8642\n'
    printf 'API_SERVER_KEY=%s\n' "$api_key"
    printf 'API_SERVER_CORS_ORIGINS=[]\n'
  } >> "$temp_env"
  chmod 600 "$temp_env"
  chown "$HERMES_UID:$HERMES_GID" "$temp_env" 2>/dev/null || true
  mv -f -- "$temp_env" "$profile_env"
}

run_setup() {
  info "Opening the official Hermes setup wizard for '$HERMES_PROFILE'."
  hermes_run -p "$HERMES_PROFILE" setup
}

configure_serve() {
  local failed=0 serve_status dashboard_url
  info "Configuring Tailnet-only HTTPS for dashboard and API..."
  compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve --bg --https=443 http://127.0.0.1:9119 || failed=1
  compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve --bg --https=8443 http://127.0.0.1:8642 || failed=1
  if (( failed )); then
    warn "Tailscale Serve needs attention (often HTTPS must be enabled for the tailnet)."
    warn "Follow the URL above if shown, then run: ./hermes-spawn.sh serve $TAILSCALE_HOSTNAME"
    return 1
  fi
  serve_status="$(compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve status)"
  printf '%s\n' "$serve_status"
  dashboard_url="$(printf '%s\n' "$serve_status" | grep -Eo 'https://[^[:space:]]+' | head -1 || true)"
  dashboard_url="${dashboard_url%/}"
  if [[ -n "$dashboard_url" ]]; then
    configure_dashboard_access "$dashboard_url"
  fi
}

configure_dashboard_access() {
  local public_url="$1" profile_env username password secret temp_env changed=0
  profile_env="$(profile_env_path)"
  mkdir -p -- "$(dirname -- "$profile_env")"
  username="$(read_control_value HERMES_DASHBOARD_BASIC_AUTH_USERNAME "$profile_env" 2>/dev/null || true)"
  password="$(read_control_value HERMES_DASHBOARD_BASIC_AUTH_PASSWORD "$profile_env" 2>/dev/null || true)"
  secret="$(read_control_value HERMES_DASHBOARD_BASIC_AUTH_SECRET "$profile_env" 2>/dev/null || true)"
  [[ -n "$username" ]] || { username=admin; changed=1; }
  [[ -n "$password" ]] || { password="$(openssl rand -hex 18)"; changed=1; }
  [[ -n "$secret" ]] || { secret="$(openssl rand -hex 32)"; changed=1; }
  if [[ "$(read_control_value HERMES_DASHBOARD_PUBLIC_URL "$profile_env" 2>/dev/null || true)" != "$public_url" ]]; then
    changed=1
  fi
  (( changed == 1 )) || return 0

  temp_env="$(mktemp "${profile_env}.tmp.XXXXXX")"
  if [[ -f "$profile_env" ]]; then
    awk '
      !/^(HERMES_DASHBOARD_PUBLIC_URL|HERMES_DASHBOARD_BASIC_AUTH_USERNAME|HERMES_DASHBOARD_BASIC_AUTH_PASSWORD|HERMES_DASHBOARD_BASIC_AUTH_SECRET)=/
    ' "$profile_env" > "$temp_env"
  fi
  {
    printf 'HERMES_DASHBOARD_PUBLIC_URL=%s\n' "$public_url"
    printf 'HERMES_DASHBOARD_BASIC_AUTH_USERNAME=%s\n' "$username"
    printf 'HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=%s\n' "$password"
    printf 'HERMES_DASHBOARD_BASIC_AUTH_SECRET=%s\n' "$secret"
  } >> "$temp_env"
  chmod 600 "$temp_env"
  chown "$HERMES_UID:$HERMES_GID" "$temp_env" 2>/dev/null || true
  mv -f -- "$temp_env" "$profile_env"
  if compose ps --status running --services | grep -Fqx hermes; then
    info "Restarting Hermes with Tailnet dashboard authentication..."
    compose restart hermes
  fi
}

spawn_instance() {
  local suggested_host cpu_total memory_total cpu_default memory_default
  local authkey api_key dir
  preflight
  install -d -m 700 "$INSTANCES_DIR"

  suggested_host="hermes-$(hostname -s 2>/dev/null | tr '[:upper:]_' '[:lower:]-' | tr -cd 'a-z0-9-' | cut -c1-50)"
  TAILSCALE_HOSTNAME="$(prompt_default 'Tailscale hostname / instance name' "$suggested_host")"
  TAILSCALE_HOSTNAME="${TAILSCALE_HOSTNAME,,}"
  validate_hostname "$TAILSCALE_HOSTNAME" || die "Use 1-63 lowercase letters, digits, or interior hyphens."
  dir="$INSTANCES_DIR/$TAILSCALE_HOSTNAME"
  [[ ! -e "$dir" ]] || die "Instance already exists: $TAILSCALE_HOSTNAME"

  HERMES_PROFILE="$(prompt_default 'Name of the default Hermes profile' 'default')"
  validate_profile "$HERMES_PROFILE" || die "Invalid/reserved profile name (letters, digits, _ and -; max 48)."

  cpu_total="$(host_cpu_count)"
  memory_total="$(host_memory_mib)"
  cpu_default="$(default_cpu_limit "$cpu_total")"
  memory_default="$(default_memory_limit "$memory_total")"
  HERMES_CPUS="$(prompt_default 'Hermes CPU limit' "$cpu_default")"
  validate_cpu "$HERMES_CPUS" || die "CPU must be a positive number such as 4 or 7.5."
  HERMES_MEMORY="$(prompt_default 'Hermes memory limit' "$memory_default")"
  validate_memory "$HERMES_MEMORY" || die "Memory must look like 8g or 16384m."

  printf '\nCreate a one-off or reusable auth key at:\n'
  printf '  https://login.tailscale.com/admin/settings/keys\n'
  authkey="$(tailscale_authkey 'Tailscale auth key: ')"

  COMPOSE_PROJECT_NAME="hermes-${TAILSCALE_HOSTNAME}"
  TAILSCALE_STATE_DIR="$dir/tailscale-state"
  TAILSCALE_AUTHKEY_FILE="$dir/tailscale-authkey"
  TS_AUTHKEY_SPEC="file:/run/secrets/tailscale-authkey"
  HERMES_DATA_DIR="$dir/hermes-data"
  if (( $(id -u) == 0 )); then HERMES_UID=10000; HERMES_GID=10000
  else HERMES_UID="$(id -u)"; HERMES_GID="$(id -g)"; fi
  HERMES_MEMORY_RESERVATION=1g
  HERMES_SHM_SIZE=1g
  HERMES_PIDS_LIMIT=4096
  HERMES_IMAGE=nousresearch/hermes-agent:latest
  TAILSCALE_IMAGE=tailscale/tailscale:latest

  install -d -m 700 "$dir" "$TAILSCALE_STATE_DIR" "$HERMES_DATA_DIR"
  printf '%s' "$authkey" > "$TAILSCALE_AUTHKEY_FILE"
  chmod 600 "$TAILSCALE_AUTHKEY_FILE"
  if (( $(id -u) == 0 )); then chown -R 10000:10000 "$HERMES_DATA_DIR"; fi
  write_control_file "$dir/control.env"
  collect_host_info "$dir/system-info.txt"
  api_key="$(openssl rand -hex 32)"

  INSTANCE_DIR="$dir"
  export INSTANCE_DIR
  load_instance "$dir"
  info "Pulling the official container images..."
  compose pull
  compose config --quiet
  compose up -d tailscale
  wait_for_tailscale
  initialize_profile "$api_key"
  if [[ -f "$STACK_DEFAULTS_FILE" && -f "$STACK_APPLY_LIB" ]] \
    && confirm 'Apply the fleet model-stack defaults (one endpoint, fallback chain, reactions off, expanded thinking)?' yes; then
    apply_stack_core no prompt
  else
    warn "Fleet model-stack skipped. Apply later with: ./hermes-spawn.sh apply-stack $TAILSCALE_HOSTNAME"
  fi
  if confirm 'Run the Hermes provider/channel setup wizard now?' yes; then
    run_setup
  else
    warn "Hermes will start, but it needs setup before it can answer. Run: ./hermes-spawn.sh setup $TAILSCALE_HOSTNAME"
  fi
  compose up -d hermes
  configure_serve || true

  # Every spawn gets webui + serve routes by default, and host-side autostart.
  webui_ensure_instance apply || warn "webui setup failed — rerun: ./hermes-spawn.sh webui-ensure $TAILSCALE_HOSTNAME"
  if command -v systemctl >/dev/null 2>&1 && (( $(id -u) == 0 )); then
    if ! systemctl is-enabled "$FLEET_SUPERVISOR_UNIT_NAME" >/dev/null 2>&1; then
      if confirm 'Enable the fleet supervisor (webui + serve routes auto-restart)?' yes; then
        supervisor_command install || warn "supervisor install failed — rerun: ./hermes-spawn.sh supervisor install"
      fi
    fi
  elif ! command -v systemctl >/dev/null 2>&1; then
    info "No systemd — start scripts/fleet-supervisor.sh manually (keep-alive task)."
  fi

  printf '\nInstance %s is running.\n' "$TAILSCALE_HOSTNAME"
  credentials_command
  printf '\nManage it with: ./hermes-spawn.sh status %s\n' "$TAILSCALE_HOSTNAME"
}

ensure_running() {
  compose up -d
  wait_for_tailscale
  # Heal webui + routes for already-vendored instances (cheap; new deployments
  # happen via webui-ensure apply / spawn).
  if [[ -f "$HERMES_DATA_DIR/hermes-webui/ctl.sh" ]]; then
    webui_ensure_instance launch || true
  elif webui_enabled [[ -n "$WEBUI_SETUP_LIB" ]]; then
    info "webui not deployed for $TAILSCALE_HOSTNAME — run: ./hermes-spawn.sh webui-ensure $TAILSCALE_HOSTNAME"
  fi
}

setup_command() {
  ensure_running
  if compose ps --status running --services | grep -Fqx hermes; then
    compose exec hermes hermes -p "$HERMES_PROFILE" setup
    compose restart hermes
  else
    run_setup
    compose up -d hermes
  fi
}

# ------------------------------------------------------------- fleet stack
# One-endpoint model stack (see stack-defaults.yaml). deploy.sh records the
# endpoint in .model-endpoint (mode 0600); every apply refreshes control.env.
STACK_DEFAULTS_FILE="$SCRIPT_DIR/stack-defaults.yaml"
stack_status_command() {
  stack_status_core
}

webui_enabled() {
  [[ -f "$STACK_DEFAULTS_FILE" ]] && grep -qE '^  enabled:.*(true|yes|1)' "$STACK_DEFAULTS_FILE" 2>/dev/null
}

# Idempotent webui deploy+launch+routes via lib/webui_setup.py.
#  $1 = "apply" (clone+deploy+env+launch+routes) or "launch" (start+routes only)
webui_ensure_instance() {
  [[ -f "$WEBUI_SETUP_LIB" ]] || { warn "webui support files missing ($WEBUI_SETUP_LIB)"; return 1; }
  webui_enabled || { info "webui disabled in stack-defaults.yaml — skipping."; return 0; }
  python3 "$WEBUI_SETUP_LIB" "${1:-apply}" \
    --home "$HERMES_DATA_DIR" \
    --instance "$TAILSCALE_HOSTNAME" \
    --control "$INSTANCE_DIR/control.env" \
    --defaults "$STACK_DEFAULTS_FILE" \
    --launch-profile "$HERMES_PROFILE" \
    --uid "$HERMES_UID" --gid "$HERMES_GID"
}

webui_ensure_command() { webui_ensure_instance apply; }
webui_status_command() {
  if [[ ! -d "$HERMES_DATA_DIR/hermes-webui" ]]; then
    echo "webui not deployed for $TAILSCALE_HOSTNAME (run: ./hermes-spawn.sh webui-ensure $TAILSCALE_HOSTNAME)"
    return 0
  fi
  python3 "$WEBUI_SETUP_LIB" status \
    --home "$HERMES_DATA_DIR" \
    --instance "$TAILSCALE_HOSTNAME" \
    --control "$INSTANCE_DIR/control.env" \
    --defaults "$STACK_DEFAULTS_FILE"
}

WEBUI_SETUP_LIB="$SCRIPT_DIR/lib/webui_setup.py"
STACK_APPLY_LIB="$SCRIPT_DIR/lib/apply_stack.py"
FLEET_SUPERVISOR_SCRIPT="$SCRIPT_DIR/scripts/fleet-supervisor.sh"
FLEET_SUPERVISOR_UNIT_SRC="$SCRIPT_DIR/systemd/hermes-fleet-supervisor.service"
FLEET_SUPERVISOR_UNIT_NAME="hermes-fleet-supervisor.service"
STACK_ENDPOINT_FILE="$SCRIPT_DIR/.model-endpoint"

model_stack_resolve() {
  # Endpoint + key from control.env, then .model-endpoint, then host Tailscale.
  if [[ -z "${MODEL_STACK_BASE_URL:-}" && -r "$STACK_ENDPOINT_FILE" ]]; then
    # shellcheck disable=SC1090
    set +u; . "$STACK_ENDPOINT_FILE"; set -u
  fi
  if [[ -z "${MODEL_STACK_BASE_URL:-}" ]]; then
    local tsip=""
    tsip="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    [[ -n "$tsip" ]] && MODEL_STACK_BASE_URL="http://${tsip}:8317/v1"
  fi
  MODEL_STACK_API_KEY="${MODEL_STACK_API_KEY:-ccs-internal-managed}"
  export MODEL_STACK_BASE_URL MODEL_STACK_API_KEY
}

model_stack_reachable() {
  [[ -n "${MODEL_STACK_BASE_URL:-}" ]] || return 1
  curl -fsS --max-time 10 -o /dev/null \
    -H "Authorization: Bearer ${MODEL_STACK_API_KEY}" \
    "${MODEL_STACK_BASE_URL%/}/models" 2>/dev/null
}

model_stack_persist() {
  local control="$INSTANCE_DIR/control.env" tmp
  tmp="$(mktemp "${control}.tmp.XXXXXX")"
  grep -v '^MODEL_STACK_' "$control" > "$tmp" || true
  printf 'MODEL_STACK_BASE_URL=%s\nMODEL_STACK_API_KEY=%s\n' \
    "$MODEL_STACK_BASE_URL" "$MODEL_STACK_API_KEY" >> "$tmp"
  chmod 600 "$tmp"
  mv -f -- "$tmp" "$control"
}

model_stack_require() {
  [[ -n "${MODEL_STACK_BASE_URL:-}" ]] || die "No model endpoint known. Run: ./deploy.sh on this host first."
  if [[ -t 0 ]]; then
    curl -fsS --max-time 10 -o /dev/null \
      -H "Authorization: Bearer ${MODEL_STACK_API_KEY}" \
      "${MODEL_STACK_BASE_URL%/}/models" || warn "Model endpoint $MODEL_STACK_BASE_URL is not reachable."
  fi
}

require_stack_tools() {
  require_command python3
  python3 -c 'import yaml' >/dev/null 2>&1 || die "PyYAML is required on this host (e.g. python3-yaml)."
  [[ -f "$STACK_DEFAULTS_FILE" ]] || die "Missing $STACK_DEFAULTS_FILE"
  [[ -f "$STACK_APPLY_LIB" ]] || die "Missing $STACK_APPLY_LIB"
}

stack_python() {
  MODEL_STACK_ENDPOINT="$MODEL_STACK_BASE_URL" MODEL_STACK_API_KEY="$MODEL_STACK_API_KEY" \
    python3 "$STACK_APPLY_LIB" "$@"
}

apply_stack_core() {
  # Apply to the resolved, loaded instance. $1 = yes (skip prompts) / no.
  local auto="${1:-no}" restart_choice="${2:-prompt}"
  require_stack_tools
  model_stack_resolve
  [[ -n "${MODEL_STACK_BASE_URL:-}" ]] || die "No model endpoint known. Run ./deploy.sh on this host first."
  if ! model_stack_reachable; then
    warn "Model endpoint $MODEL_STACK_BASE_URL is not reachable (CLIProxy down or wrong key)."
    warn "Run ./deploy.sh on this host first, then retry."
    if [[ "$auto" == yes ]]; then
      die "Endpoint unreachable; aborting apply-stack."
    else
      confirm 'Apply the stack anyway (Hermes will fail until the endpoint is back)?' no || die "Aborted."
    fi
  fi
  local apply_out
  apply_out="$(stack_python apply \
    --home "$HERMES_DATA_DIR" \
    --launch-profile "$HERMES_PROFILE" \
    --base-url "$MODEL_STACK_BASE_URL" \
    --api-key "$MODEL_STACK_API_KEY" \
    --defaults "$STACK_DEFAULTS_FILE" \
    --uid "$HERMES_UID" --gid "$HERMES_GID")"
  printf '%s\n' "$apply_out"
  local changes=0
  if printf '%s\n' "$apply_out" | grep -q '^  wrote '; then changes=1; fi
  model_stack_persist
  if (( changes == 1 )); then
    stack_status_core --offline >/dev/null 2>&1 || warn "Post-apply audit found drift; run: ./hermes-spawn.sh stack-status $TAILSCALE_HOSTNAME"
  fi
  if (( changes == 0 )); then
    info "No drift on disk — everything already matches stack-defaults.yaml."
    return 0
  fi
  if compose ps --status running --services 2>/dev/null | grep -Fqx hermes; then
    local do_restart=no
    if [[ "$restart_choice" == yes ]]; then do_restart=yes
    elif [[ "$restart_choice" == no ]]; then do_restart=no
    elif [[ -t 0 ]]; then
      confirm 'Hermes is running. Restart it now so the new stack takes effect?' yes && do_restart=yes
    fi
    if [[ "$do_restart" == yes ]]; then
      info "Restarting Hermes to activate the stack..."
      compose restart hermes
    else
      warn "Hermes is still on the old config. Run: ./hermes-spawn.sh restart $TAILSCALE_HOSTNAME"
    fi
  fi
}

stack_status_core() {
  # Audit only (read-only). $1 = optional --offline.
  require_stack_tools
  model_stack_resolve
  [[ -n "${MODEL_STACK_BASE_URL:-}" ]] || die "No model endpoint known. Run ./deploy.sh on this host first."
  local reachable=yes
  if model_stack_reachable; then reachable=yes; else reachable=no; fi
  stack_python status \
    --home "$HERMES_DATA_DIR" \
    --launch-profile "$HERMES_PROFILE" \
    --base-url "$MODEL_STACK_BASE_URL" \
    --api-key "$MODEL_STACK_API_KEY" \
    --defaults "$STACK_DEFAULTS_FILE" ${1:+--quiet}
  local python_rc=$?
  if [[ "$reachable" == no ]]; then
    warn "Model endpoint $MODEL_STACK_BASE_URL unreachable (no live health check)."
  else
    info "Model endpoint OK: $MODEL_STACK_BASE_URL"
  fi
  return $python_rc
}

apply_stack_command() {
  local auto=no restart_choice=prompt
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -y|--yes) auto=yes ;;
      --restart) restart_choice=yes ;;
      --no-restart) restart_choice=no ;;
      *) die "Unknown apply-stack option: $1" ;;
    esac
    shift
  done
  apply_stack_core "$auto" "$restart_choice"
}

stack_status_command() {
  stack_status_core
}

credentials_command() {
  local profile_env key serve_status dashboard_url api_url dashboard_user dashboard_password
  profile_env="$(profile_env_path)"
  key="$(awk -F= '$1 == "API_SERVER_KEY" {sub(/^[^=]*=/, ""); print; exit}' "$profile_env" 2>/dev/null || true)"
  dashboard_user="$(read_control_value HERMES_DASHBOARD_BASIC_AUTH_USERNAME "$profile_env" 2>/dev/null || true)"
  dashboard_password="$(read_control_value HERMES_DASHBOARD_BASIC_AUTH_PASSWORD "$profile_env" 2>/dev/null || true)"
  serve_status="$(compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve status 2>/dev/null || true)"
  dashboard_url="$(printf '%s\n' "$serve_status" | grep -Eo 'https://[^[:space:]]+' | head -1 || true)"
  api_url="$(printf '%s\n' "$serve_status" | grep -Eo 'https://[^[:space:]]+:8443' | head -1 || true)"
  printf 'Dashboard: %s\n' "${dashboard_url:-Tailnet HTTPS not configured; run the serve command}"
  if [[ -n "$api_url" ]]; then
    printf 'API base:  %s/v1\n' "$api_url"
  else
    printf 'API base:  Tailnet HTTPS not configured; run the serve command\n'
  fi
  printf 'API key:   %s\n' "${key:-not configured}"
  printf 'Web user:  %s\n' "${dashboard_user:-not configured}"
  printf 'Web pass:  %s\n' "${dashboard_password:-not configured}"
  printf 'Profile:   %s\n' "$HERMES_PROFILE"
}

# ---------------------------------------------------------------- supervisor
# Host-side fleet supervisor: one systemd unit keeps webui + serve routes alive
# across container restarts for every running instance. Operator-approved default.
supervisor_command() {
  local sub="${1:-status}"
  if ! command -v systemctl >/dev/null 2>&1; then
    warn "No systemd on this host. Install manually:"
    printf '  macOS launchd equivalent: run scripts/fleet-supervisor.sh as a keep-alive agent.\n'
    return 1
  fi
  case "$sub" in
    install)
      require_command sed
      local unit_dest="/etc/systemd/system/$FLEET_SUPERVISOR_UNIT_NAME"
      [[ -f "$FLEET_SUPERVISOR_UNIT_SRC" ]] || die "missing $FLEET_SUPERVISOR_UNIT_SRC"
      if (( $(id -u) != 0 )); then die "supervisor install needs root (unit write + systemctl)."; fi
      sed "s|/root/hermes-spawning|$SCRIPT_DIR|g" "$FLEET_SUPERVISOR_UNIT_SRC" > "$unit_dest"
      systemctl daemon-reload
      systemctl enable --now "$FLEET_SUPERVISOR_UNIT_NAME"
      systemctl --no-pager --lines=5 status "$FLEET_SUPERVISOR_UNIT_NAME" || true
      ;;
    enable)  systemctl enable --now "$FLEET_SUPERVISOR_UNIT_NAME" ;;
    disable) systemctl disable --now "$FLEET_SUPERVISOR_UNIT_NAME" ;;
    status)
      systemctl --no-pager --lines=10 status "$FLEET_SUPERVISOR_UNIT_NAME" || true
      [[ -f "$INSTANCES_DIR/.supervisor.log" ]] && tail -n 15 "$INSTANCES_DIR/.supervisor.log" || true
      ;;
    *) die "supervisor subcommands: install enable disable status" ;;
  esac
}

reauth_command() {
  local authkey
  printf 'Create a fresh one-off or reusable auth key at:\n'
  printf '  https://login.tailscale.com/admin/settings/keys\n'
  authkey="$(tailscale_authkey 'New Tailscale auth key: ')"
  printf '%s' "$authkey" > "$TAILSCALE_AUTHKEY_FILE"
  chmod 600 "$TAILSCALE_AUTHKEY_FILE"
  compose up -d --force-recreate tailscale hermes
  wait_for_tailscale
  configure_serve || true
}

main() {
  local command="${1:-spawn}" requested="${2:-}" dir
  case "$command" in
    -h|--help|help) usage; return ;;
    spawn|create) spawn_instance; return ;;
    list|ls) preflight; list_instances; return ;;
    preflight) preflight; return ;;
    supervisor) supervisor_command "${@:2}"; return ;;
    start|stop|restart|status|logs|setup|config|chat|shell|doctor|serve|reauth|credentials|update|apply-stack|stack-status|info|webui-ensure|webui-status) ;;
    *) usage >&2; die "Unknown command: $command" ;;
  esac

  preflight
  dir="$(resolve_instance "$requested")"
  readonly INSTANCE_DIR="$dir"
  export INSTANCE_DIR
  load_instance "$dir"

  # Prevent two lifecycle operations from racing on one instance. Time out
  # loudly with the holder named, instead of waiting forever.
  exec 9>"$INSTANCE_DIR/.operation.lock"
  if ! flock -w 20 9 2>/dev/null; then
    holders="$(fuser "$INSTANCE_DIR/.operation.lock" 2>/dev/null | xargs || true)"
    die "Instance $TAILSCALE_HOSTNAME is busy (lock held by PID(s): ${holders:-unknown}). Close that operation and retry."
  fi

  case "$command" in
    start) ensure_running; configure_serve || true ;;
    stop) compose down ;;
    restart) compose restart; wait_for_tailscale ;;
    status)
      compose ps
      compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock status || true
      compose exec -T tailscale tailscale --socket=/tmp/tailscaled.sock serve status || true
      ;;
    logs) compose logs --follow --tail=150 ;;
    setup|config) setup_command ;;
    chat) ensure_running; compose exec hermes hermes -p "$HERMES_PROFILE" ;;
    shell) ensure_running; compose exec hermes bash ;;
    doctor) ensure_running; compose exec -T hermes hermes -p "$HERMES_PROFILE" doctor ;;
    serve) ensure_running; configure_serve ;;
    reauth) reauth_command ;;
    credentials) ensure_running; credentials_command ;;
    apply-stack) apply_stack_command "${@:3}" ;;
    webui-ensure) webui_ensure_command ;;
    webui-status) webui_status_command ;;
    stack-status) stack_status_command; exit $? ;;
    update)
      info "Pulling current images and recreating $TAILSCALE_HOSTNAME..."
      compose pull
      compose up -d --remove-orphans
      wait_for_tailscale
      configure_serve || true
      ;;
    info) sed -n '1,200p' "$INSTANCE_DIR/system-info.txt" ;;
  esac
}

main "$@"
