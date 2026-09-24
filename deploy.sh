#!/usr/bin/env bash
# =============================================================================
# deploy.sh — host model stack for hermes-spawning
#
# Provisions and repairs ONE reliable endpoint on this host:
#
#   CCS (Claude Codex Switch)  ->  CLIProxyAPI on :8317 (OpenAI-compatible)
#     - Codex OAuth      (GPT-6 Astra, high effort)
#     - Claude OAuth     (Claude Opus 5 + the Claude family)
#     - Chutes API key   (Kimi K3, DeepSeek V4 Flash 0731, GLM-5.2)
#   - one bearer fleet key, recorded in .model-endpoint (mode 0600)
#   - optional systemd persistence so the endpoint survives reboots
#
# Every spawned Hermes instance then relies on that endpoint (+ its fallback
# chain) via ./hermes-spawn.sh apply-stack.
#
# Idempotent: re-run any time to repair. Destructive steps prompt first
# unless --yes is given; with --yes OAuth is verified but NOT minted
# (OAuth login needs a human + browser), and the script dies if a token
# the stack needs is absent.
#
# Usage:
#   ./deploy.sh                      Interactive guided setup
#   ./deploy.sh --yes|-y             Accept defaults; verify-only OAuth
#   ./deploy.sh --verify-only        Audit the host stack; exit 1 on problems
#   ./deploy.sh --reauth             Force re-doing both OAuth logins
#   ./deploy.sh --skip-oauth         Leave OAuth tokens untouched
#   ./deploy.sh --skip-systemd       Do not touch the systemd unit
#   ./deploy.sh --with-prime-models  Also register the endpoint into
#                                    ~/.prime/agent/models.json
#   CHUTES_API_KEY=cpk_... ./deploy.sh -y   Provide secrets via env
# =============================================================================
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
STACK_DEFAULTS_FILE="$SCRIPT_DIR/stack-defaults.yaml"
ENDPOINT_FILE="$SCRIPT_DIR/.model-endpoint"
CCS_HOME="${CCS_HOME:-$HOME/.ccs}"
PROXY_HOME="$CCS_HOME/cliproxy"
PROXY_CONFIG="$PROXY_HOME/config.yaml"
PROXY_AUTH_DIR="$PROXY_HOME/auth"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()    { printf '  \033[1;32mok\033[0m   %s\n' "$*"; }
warn()  { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
step()  { printf '\n\033[1;36m[%s]\033[0m %s\n' "$1" "$2"; }
die()   { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

YES=0; VERIFY_ONLY=0; FORCE_REAUTH=0; SKIP_OAUTH=0; SKIP_SYSTEMD=0; WITH_PRIME=0
CHUTES_API_KEY="${CHUTES_API_KEY:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes) YES=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    --reauth) FORCE_REAUTH=1 ;;
    --skip-oauth) SKIP_OAUTH=1 ;;
    --skip-systemd) SKIP_SYSTEMD=1 ;;
    --with-prime-models) WITH_PRIME=1 ;;
    --chutes-key) shift; CHUTES_API_KEY="${1:-}" ;;
    -h|--help) sed -n '1,45p' "$0"; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
  shift
done

confirm() { # confirm "question" [yes|no-default]; --yes answers with the default
  local prompt="$1" default="${2:-yes}" answer suffix
  if (( YES == 1 )) || [[ ! -t 0 ]]; then
    [[ "$default" == yes ]] && return 0 || return 1
  fi
  if [[ "$default" == yes ]]; then suffix='[Y/n]'; else suffix='[y/N]'; fi
  read -r -p "$prompt $suffix " answer
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]
}
prompt_default() {
  local prompt="$1" default="$2" value=""
  if (( YES == 1 )) || [[ ! -t 0 ]]; then printf '%s' "$default"; return; fi
  read -r -p "$prompt [$default]: " value
  printf '%s' "${value:-$default}"
}
prompt_secret() {
  local prompt="$1" value=""
  if (( YES == 1 )) || [[ ! -t 0 ]]; then printf '%s' ""; return; fi
  read -r -s -p "$prompt" value; printf '\n' >&2; printf '%s' "$value"
}
mask() {
  local s="${1:-}"
  if (( ${#s} <= 10 )); then printf '****%s' "${s: -2}"
  else printf '%s****%s' "${s:0:6}" "${s: -4}"; fi
}

require() { command -v "$1" >/dev/null 2>&1 || die "required command missing: $1"; }

CCS_BIN="$(command -v ccs 2>/dev/null || true)"
proxy_port() { awk '/^port:/ {print $2; exit}' "$PROXY_CONFIG" 2>/dev/null || true; }
oauth_file() { # oauth_file claude|codex -> token path or empty
  local svc="$1" f
  shopt -s nullglob
  for f in "$PROXY_AUTH_DIR"/$svc-*.json; do printf '%s' "$f"; return 0; done
  shopt -u nullglob
  printf ''
}

# ---------------------------------------------------------------------------
step 0 "Preflight"
[[ $EUID -eq 0 ]] || warn "not running as root — systemd and package installs will fail"
[[ -f "$STACK_DEFAULTS_FILE" ]] || die "Missing $STACK_DEFAULTS_FILE — run from inside the hermes-spawning repo."
require curl; require awk; require python3; require openssl
python3 -c 'import yaml' 2>/dev/null || die "PyYAML required on this host (e.g. apt install python3-yaml)."
NODE_OK=0
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || echo 0)"
  (( NODE_MAJOR >= 18 )) && NODE_OK=1
fi
(( NODE_OK == 1 )) && ok "node $(node --version) / npm $(npm --version)" || warn "Node.js 18+ and npm are required for CCS"

# ---------------------------------------------------------------------------
step 1 "CCS + CLIProxy binary"
if (( VERIFY_ONLY == 0 )); then
  if [[ -z "$CCS_BIN" ]]; then
    (( NODE_OK == 1 )) || die "Install Node.js 18+ first (nvm recommended), then re-run deploy.sh."
    info "Installing @kaitranntt/ccs globally..."
    npm install -g @kaitranntt/ccs --no-audit --no-fund
    hash -r
    CCS_BIN="$(command -v ccs)"
    [[ -n "$CCS_BIN" ]] || die "ccs install failed"
  else
    ok "ccs found ($CCS_BIN)"
    confirm "Upgrade CCS to the latest version?" no && {
      npm install -g @kaitranntt/ccs --no-audit --no-fund
      hash -r
    }
  fi
  info "Fetching/refreshing the CLIProxy binary..."
  ccs cliproxy --latest || die "CLIProxy binary fetch failed"
else
  [[ -n "$CCS_BIN" ]] && ok "ccs found: $CCS_BIN" || die "ccs is not installed"
  [[ -d "$PROXY_HOME" ]] || die "$PROXY_HOME missing"
fi

# ---------------------------------------------------------------------------
step 2 "OAuth accounts (Codex = GPT-6 Astra, Claude = Opus 5 + family)"
redo_or_skip() { # -> 0 (re)auth, 1 keep existing
  local svc="$1" f email
  f="$(oauth_file "$svc")"
  if [[ -n "$f" && $FORCE_REAUTH -eq 0 ]]; then
    email="$(basename "$f" | sed -E "s/^${svc}-[a-z0-9]+-(.*)(-pro)?\.json\$/\\1/")"
    ok "${svc} OAuth present (${email})"
    if confirm "Re-authenticate the ${svc} account anyway?" no; then return 0; fi
    return 1
  fi
  return 0
}
if (( SKIP_OAUTH == 0 )) && (( VERIFY_ONLY == 0 )); then
  if redo_or_skip codex; then
    cat <<'GUIDE'

  About to run: ccs codex --auth
  --------------------------------
  This registers your ChatGPT (Codex) account. A URL prints below.
  On a desktop the browser opens by itself; on a headless host, copy the URL
  to any browser, sign in with the ChatGPT Pro/Team account, then paste the
  final redirect URL / code back into this terminal when prompted.
GUIDE
    ccs codex --auth
    [[ -n "$(oauth_file codex)" ]] || die "Codex OAuth did not finish; re-run deploy.sh."
    ok "Codex OAuth stored"
  fi
  if redo_or_skip claude; then
    cat <<'GUIDE'

  About to run: ccs claude --auth
  --------------------------------
  This registers your Claude (Anthropic) account. A URL prints below.
  Open it in a browser, sign in, and paste the final redirect URL / code back
  into this terminal when prompted.
GUIDE
    ccs claude --auth
    [[ -n "$(oauth_file claude)" ]] || die "Claude OAuth did not finish; re-run deploy.sh."
    ok "Claude OAuth stored"
  fi
else
  [[ -n "$(oauth_file codex)" ]]  && ok "codex token present" \
    || die "codex OAuth token missing — run: ${CCS_BIN:-ccs} codex --auth, then re-run deploy.sh"
  [[ -n "$(oauth_file claude)" ]] && ok "claude token present" \
    || die "claude OAuth token missing — run: ${CCS_BIN:-ccs} claude --auth, then re-run deploy.sh"
fi

# ---------------------------------------------------------------------------
step 3 "Chutes OpenAI-compatible upstream (idempotent append)"
[[ -f "$PROXY_CONFIG" ]] || die "$PROXY_CONFIG missing (run: $CCS_BIN cliproxy start once, then re-run)"

CURRENT_CHUTES_KEY="$(python3 - "$PROXY_CONFIG" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
m = re.search(r'^openai-compatibility:\n(.*?)(?=^\S|\Z)', text, re.M | re.S)
if m:
    km = re.search(r'-\s*api-key:\s*([^\s]+)', m.group(1))
    if km:
        print(km.group(1))
PY
)"
if [[ -z "$CURRENT_CHUTES_KEY" ]]; then ok "no chutes block yet"
else ok "chutes block found (key $(mask "$CURRENT_CHUTES_KEY"))"; fi

if [[ -z "$CHUTES_API_KEY" && -n "$CURRENT_CHUTES_KEY" ]]; then
  if (( VERIFY_ONLY == 0 )) && ! confirm "Keep the existing Chutes API key ($(mask "$CURRENT_CHUTES_KEY"))?" yes; then
    CURRENT_CHUTES_KEY=""
  else
    CHUTES_API_KEY="$CURRENT_CHUTES_KEY"
  fi
fi
if [[ -z "$CHUTES_API_KEY" ]]; then
  CHUTES_API_KEY="$(prompt_secret 'Chutes API key (https://chutes.ai -> API Keys, starts cpk_): ')"
fi
[[ -z "$CHUTES_API_KEY" ]] && die "A Chutes API key is required for Kimi/DeepSeek/GLM (CHUTES_API_KEY env or prompt)."
[[ "$CHUTES_API_KEY" == cpk_* ]] || warn "key does not start with cpk_ — double-check: $(mask "$CHUTES_API_KEY")"

if (( VERIFY_ONLY == 0 )); then
  PROXY_CONFIG_PATH="$PROXY_CONFIG" CHUTES_KEY="$CHUTES_API_KEY" DEFAULTS_PATH="$STACK_DEFAULTS_FILE" \
  python3 - <<'PY'
import os, re, shutil, time
import yaml

path = os.environ["PROXY_CONFIG_PATH"]
key = os.environ["CHUTES_KEY"]
defaults = yaml.safe_load(open(os.environ["DEFAULTS_PATH"]))
ch = defaults["chutes"]

lines = [f"        - name: {m['name']}\n          alias: {m['alias']}" for m in ch["models"]]
block = ("openai-compatibility:\n"
         f"  - name: {ch['upstream_name']}\n"
         f"    base-url: {ch['base_url']}\n"
         "    api-key-entries:\n"
         f"      - api-key: {key}\n"
         "    models:\n" + "\n".join(lines) + "\n")

text = open(path).read() if os.path.exists(path) else ""
m = re.search(r"^openai-compatibility:\n(.*?)(?=^\S|\Z)", text, re.M | re.S)
write = True
if m:
    sect = m.group(0)
    have_name = re.search(r'-\s*name:\s*' + re.escape(ch["upstream_name"]) + r'\s*(\n|$)', sect)
    have_all = all(re.search(r'alias:\s*' + re.escape(mm["alias"]) + r'\s*(\n|$)', sect) for mm in ch["models"])
    have_key = key in sect
    if have_name and have_all and have_key:
        write = False
        print("chutes upstream already complete")
    else:
        # Replace the whole openai-compatibility section with the canonical block.
        text = (text[:m.start()] + text[m.end():]).rstrip("\n") + "\n"
if write:
    if os.path.exists(path) and os.path.getsize(path) > 0:
        shutil.copy2(path, f"{path}.bak-deploy-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    with open(path, "a") as fh:
        fh.write("\n" + block)
    print("chutes upstream written (2-space-indented list items — CCS regen-safe)")
PY
  CONFIG_TXT="$(cat "$PROXY_CONFIG")"
  grep -q '^openai-compatibility:' <<<"$CONFIG_TXT" || die "post-write verification failed: openai-compatibility missing"
  grep -q -- "$CHUTES_API_KEY" <<<"$CONFIG_TXT" || die "post-write verification failed: chutes key missing"
  for alias in kimi-k3 kimi-k2.6 glm-5.2 deepseek-v3.2 deepseek-v4-flash-0731; do
    grep -q "alias: $alias" <<<"$CONFIG_TXT" || die "post-write verification failed: alias $alias missing"
  done
  ok "chutes upstream verified in $PROXY_CONFIG"
fi

# ---------------------------------------------------------------------------
step 4 "Fleet bearer key for the one endpoint"
FLEET_KEY=""
if [[ -f "$ENDPOINT_FILE" ]]; then
  set +u; . "$ENDPOINT_FILE"; set -u
  FLEET_KEY="${MODEL_STACK_API_KEY:-}"
fi
if [[ -z "$FLEET_KEY" ]]; then
  FLEET_KEY="hermes-fleet-$(openssl rand -hex 16)"
fi
if (( VERIFY_ONLY == 0 )); then
  PROXY_CONFIG_PATH="$PROXY_CONFIG" FLEET_KEY="$FLEET_KEY" python3 - <<'PY'
import os, re, shutil, time
path = os.environ["PROXY_CONFIG_PATH"]
key = os.environ["FLEET_KEY"]
text = open(path).read()
if key in text:
    print("fleet key already in api-keys")
else:
    shutil.copy2(path, f"{path}.bak-deploy-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    m = re.search(r'^api-keys:\n((?:[ \t]+-.*\n)+)', text, re.M)
    if m:
        text = text[:m.end(1)] + f'  - "{key}"\n' + text[m.end(1):]
    else:
        pos = text.find("\n# OAuth tokens directory")
        ins = f'\n# API keys for CCS and user-added external requests\napi-keys:\n  - "ccs-internal-managed"\n  - "{key}"\n'
        if pos >= 0:
            text = text[:pos] + "\n" + ins + text[pos + 1:]
        else:
            text = text.rstrip("\n") + "\n" + ins
    with open(path, "w") as fh:
        fh.write(text)
    print("fleet key added to api-keys (2-space-indented — CCS regen-safe)")
PY
  grep -q -- "$FLEET_KEY" "$PROXY_CONFIG" || die "post-write verification failed: fleet key missing"
  ok "fleet key in cliproxy api-keys ($(mask "$FLEET_KEY"))"
else
  [[ -n "$FLEET_KEY" ]] || die "$ENDPOINT_FILE missing — run deploy.sh once (without --verify-only) to mint the fleet key."
  grep -q -- "$FLEET_KEY" "$PROXY_CONFIG" && ok "fleet key in api-keys" \
    || die "fleet key from $ENDPOINT_FILE not present in $PROXY_CONFIG api-keys — re-run deploy.sh"
fi

# ---------------------------------------------------------------------------
step 5 "Start/restart the proxy"
if (( VERIFY_ONLY == 0 )); then
  if ccs cliproxy status 2>/dev/null | grep -q Running; then
    ccs cliproxy restart >/dev/null && ok "cliproxy restarted"
  else
    ccs cliproxy start >/dev/null && ok "cliproxy started"
  fi
else
  ccs cliproxy status 2>/dev/null | grep -q Running && ok "cliproxy running" || die "cliproxy not running"
  [[ -z "$FLEET_KEY" ]] && die "$ENDPOINT_FILE missing; run deploy.sh once without --verify-only"
fi

PORT="$(proxy_port)"; PORT="${PORT:-8317}"
info "polling http://127.0.0.1:$PORT/v1/models with the fleet key..."
READY=0
for _ in $(seq 1 45); do
  if curl -fsS --max-time 5 -H "Authorization: Bearer $FLEET_KEY" \
      "http://127.0.0.1:$PORT/v1/models" >/tmp/.deploy_models.$$.json 2>/dev/null; then
    READY=1; break
  fi
  sleep 2
done
(( READY == 1 )) || die "proxy did not come up on port $PORT with the fleet key"
ok "endpoint answering with the fleet key"

step 6 "Verify required models"
python3 - /tmp/.deploy_models.$$.json "$STACK_DEFAULTS_FILE" <<'PY'
import json, sys, yaml
have = {m["id"] for m in json.load(open(sys.argv[1]))["data"]}
want = yaml.safe_load(open(sys.argv[2]))["required_endpoint_models"]
missing = [m for m in want if m not in have]
if missing:
    print("MISSING models:", ", ".join(missing), file=sys.stderr)
    print("Fix the upstream that serves them (Codex OAuth, Claude OAuth, Chutes key), then re-run deploy.sh.", file=sys.stderr)
    sys.exit(1)
print("served:", ", ".join(want))
PY
rm -f /tmp/.deploy_models.$$.json
ok "model catalog complete"

# ---------------------------------------------------------------------------
step 7 "Publish the endpoint for the fleet"
TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
if [[ -z "$TS_IP" ]]; then
  warn "No Tailscale IPv4 on this host."
  warn "Hermes instances reach the host over the tailnet; without Tailscale they need a LAN/bridge IP instead."
  TS_IP="$(prompt_default 'Host IP for the fleet endpoint (reachable from Docker containers)' '172.17.0.1')"
fi
ENDPOINT_URL="http://$TS_IP:$PORT/v1"
if ss -tln 2>/dev/null | grep -qE "(0\.0\.0\.0|\*)[:.]${PORT} +"; then
  ok "proxy listens on *:$PORT (reachable at $TS_IP)"
else
  die "cliproxy is NOT listening on 0.0.0.0:$PORT — containers cannot reach a 127.0.0.1-bound proxy. Fix config and re-run."
fi
if (( VERIFY_ONLY == 0 )); then
  {
    printf '# Written by deploy.sh %s — consumed by hermes-spawn.sh apply-stack\n' "$(date -u +%FT%TZ)"
    printf 'MODEL_STACK_BASE_URL=%s\n' "$ENDPOINT_URL"
    printf 'MODEL_STACK_API_KEY=%s\n' "$FLEET_KEY"
  } > "$ENDPOINT_FILE"
  chmod 600 "$ENDPOINT_FILE"
  ok "wrote $ENDPOINT_FILE"
else
  [[ -f "$ENDPOINT_FILE" ]] && grep -q "^MODEL_STACK_BASE_URL=http://$TS_IP:$PORT/v1\$" "$ENDPOINT_FILE" \
    && ok "endpoint file matches this host" || warn "$ENDPOINT_FILE missing or stale (re-run without --verify-only)"
fi

# ---------------------------------------------------------------------------
step 8 "Persistence across reboots (systemd)"
UNIT_NAME="ccs-cliproxy.service"
if [[ ! -d /run/systemd/system ]]; then
  SKIP_SYSTEMD=1
  warn "systemd not detected — skipping persistence (fallback: add '@reboot ${CCS_BIN:-ccs} cliproxy start >/dev/null 2>&1' to root crontab)"
fi
if (( SKIP_SYSTEMD == 0 )) && (( VERIFY_ONLY == 0 )); then
  if confirm "Install $UNIT_NAME so the model endpoint starts at boot?" yes; then
    cat > "/etc/systemd/system/$UNIT_NAME" <<UNIT
[Unit]
Description=CCS-managed CLIProxyAPI (fleet model endpoint)
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=${CCS_BIN} cliproxy start
ExecStop=${CCS_BIN} cliproxy stop

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable "$UNIT_NAME" >/dev/null 2>&1 || warn "enable failed"
    if ! ccs cliproxy status 2>/dev/null | grep -q Running; then
      systemctl start "$UNIT_NAME" || warn "start via systemd failed (proxy may already be up)"
    fi
    systemctl is-enabled --quiet "$UNIT_NAME" && ok "$UNIT_NAME enabled" || warn "$UNIT_NAME not enabled"
  else
    warn "systemd persistence skipped"
  fi
elif (( SKIP_SYSTEMD == 0 )); then
  systemctl is-enabled --quiet "$UNIT_NAME" 2>/dev/null \
    && ok "$UNIT_NAME enabled" || warn "$UNIT_NAME NOT enabled (endpoint will not restart on reboot)"
fi

# ---------------------------------------------------------------------------
step 9 "Optional: register the endpoint for Prime Agent"
if (( WITH_PRIME == 1 )) || { (( VERIFY_ONLY == 0 )) && [[ -t 0 ]] && confirm "Register the endpoint into ~/.prime/agent/models.json (merge-only, backup first)?" no; }; then
  PRIME_MODELS_HOME="${HOME}/.prime/agent" ENDPOINT_URL="$ENDPOINT_URL" FLEET_KEY="$FLEET_KEY" \
  python3 - <<'PY'
import json, os, shutil, time

home = os.environ["PRIME_MODELS_HOME"]
endpoint = os.environ["ENDPOINT_URL"].rstrip("/")
if endpoint.endswith("/v1"):
    endpoint = endpoint[:-3]
key = os.environ["FLEET_KEY"]
path = os.path.join(home, "models.json")
os.makedirs(home, exist_ok=True)

MODELS = [
  {"id": "claude-fable-5",            "name": "Claude Fable 5 (CLIProxy)",        "reasoning": True, "thinkingLevelMap": {"xhigh": "xhigh", "max": "max"}, "input": ["text", "image"], "contextWindow": 1000000, "maxTokens": 128000},
  {"id": "claude-opus-5",             "name": "Claude Opus 5 (CLIProxy)",         "reasoning": True, "thinkingLevelMap": {"xhigh": "xhigh", "max": "max"}, "input": ["text", "image"], "contextWindow": 1000000, "maxTokens": 128000},
  {"id": "claude-sonnet-5",           "name": "Claude Sonnet 5 (CLIProxy)",       "reasoning": True, "thinkingLevelMap": {"xhigh": "xhigh", "max": "max"}, "input": ["text", "image"], "contextWindow": 1000000, "maxTokens": 128000},
  {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5 (CLIProxy)",      "reasoning": True, "input": ["text", "image"], "contextWindow": 200000, "maxTokens": 64000},
  {"id": "gpt-6-astra",               "name": "GPT-6 Astra (Codex via CLIProxy)", "reasoning": True, "thinkingLevelMap": {"high": "high", "xhigh": "xhigh", "max": "max"}, "input": ["text"], "contextWindow": 1000000, "maxTokens": 128000},
  {"id": "kimi-k3",                   "name": "Kimi K3 (Chutes)",                 "reasoning": True, "thinkingLevelMap": {"max": "max"}, "input": ["text"], "contextWindow": 262144, "maxTokens": 64000},
  {"id": "deepseek-v4-flash-0731",    "name": "DeepSeek V4 Flash 0731 (Chutes)", "reasoning": True, "thinkingLevelMap": {"max": "max"}, "input": ["text"], "contextWindow": 128000, "maxTokens": 64000},
  {"id": "glm-5.2",                   "name": "GLM-5.2 (Chutes)",                 "reasoning": True, "thinkingLevelMap": {"max": "max"}, "input": ["text"], "contextWindow": 200000, "maxTokens": 64000},
]

target = os.path.realpath(path) if os.path.islink(path) else path
root, mode = {}, 0o600
if os.path.exists(target):
    mode = 0o777 & os.stat(target).st_mode
    try:
        root = json.load(open(target))
    except Exception:
        raise SystemExit(f"{target}: invalid JSON; nothing changed")
if not isinstance(root, dict):
    raise SystemExit(f"{target} must contain a JSON object; nothing changed")
prov = root.setdefault("providers", {})
if not isinstance(prov, dict):
    raise SystemExit(f"{target}.providers must be an object; nothing changed")
cur = prov.get("cliproxy") or {}
if not isinstance(cur, dict):
    raise SystemExit(f"{target}.providers.cliproxy must be an object; nothing changed")

old_by_id = {m["id"]: m for m in (cur.get("models") or []) if isinstance(m, dict) and m.get("id")}
wanted = {m["id"] for m in MODELS}
merged = [{**old_by_id.get(m["id"], {}), **m} for m in MODELS] + \
         [m for m in (cur.get("models") or []) if isinstance(m, dict) and m.get("id") not in wanted]
nextprov = {**cur, "baseUrl": endpoint, "api": "anthropic-messages", "apiKey": key, "authHeader": True, "models": merged}
if cur == nextprov:
    print("models.json already up to date")
else:
    if os.path.exists(target):
        shutil.copy2(target, f"{target}.bak.{time.strftime('%Y%m%dT%H%M%S')}")
    prov["cliproxy"] = nextprov
    tmp = f"{target}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(root, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, target)
    os.chmod(target, mode)
    print(f"registered endpoint into {target}")
PY
  ok "Prime Agent models.json merged"
else
  echo "  (skipped) Prime Agent models.json registration"
fi

# ---------------------------------------------------------------------------
step 10 "Summary"
cat <<SUMMARY

  One endpoint:   $ENDPOINT_URL
  Bearer:         $FLEET_KEY
  OAuth:          codex + claude  (tokens under $PROXY_AUTH_DIR)
  Chutes:         kimi-k3 / glm-5.2 / deepseek-v4-flash-0731 (+ k2.6, v3.2)
  Fleet record:   $ENDPOINT_FILE  (mode 0600)
  Control panel:  http://127.0.0.1:$PORT/management.html  (ccs cliproxy status)

  Note: OAuth accounts over quota return straight 429s — the host endpoint
  does not auto-switch models. Hermes instances handle model fallback
  themselves (Opus 5 -> Kimi K3 -> DeepSeek -> GLM-5.2), applied by:
      ./hermes-spawn.sh apply-stack <instance> [ --restart ]
  Audit any instance with:
      ./hermes-spawn.sh stack-status <instance>

SUMMARY
if (( VERIFY_ONLY == 1 )); then ok "host stack verification passed"; fi
