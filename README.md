# Hermes Spawning

`hermes-spawning` creates independent, persistent pairs of
[Nous Research Hermes Agent](https://github.com/NousResearch/hermes-agent) and
[Tailscale](https://tailscale.com/docs/features/containers/docker). Each pair
has its own identity, state, resource limits, credentials, and Docker Compose
project.

## Quick start

```bash
cd /root/hermes-spawning
./deploy.sh                 # one-time per host: the model endpoint
./hermes-spawn.sh spawn     # create an instance (fleet defaults are applied)
```

The wizard asks for:

1. A Tailscale hostname (also the local instance name).
2. The Hermes profile that should be active by default.
3. CPU and memory limits, prefilled with generous host-aware defaults.
4. A Tailscale auth key. If a reusable key is installed as described below,
   the launcher reads it automatically; otherwise it prompts securely.

It then launches the official Hermes setup wizard so you can choose the model
provider, credentials, messaging channels, tools, and the rest of the agent
configuration. Secrets are entered into that upstream wizard rather than into
this project.

## Administrator: reusable Tailscale enrollment key

A Tailnet administrator can create one tagged, reusable auth key for spawning
multiple instances:

1. Open <https://login.tailscale.com/admin/settings/keys> and generate an auth
   key with **Reusable** enabled. Apply the narrowest device tag and practical
   expiry allowed by your Tailnet policy.
2. Copy `.tailscale-authkey.example` to `.tailscale-authkey`, replace its
   contents with only the real `tskey-...` value, and restrict access:

   ```bash
   cp .tailscale-authkey.example .tailscale-authkey
   chmod 600 .tailscale-authkey
   ```

3. Run `./hermes-spawn.sh spawn` or `./hermes-spawn.sh reauth HOSTNAME`. The
   launcher automatically uses the shared key. Its per-instance copy is
   emptied after the node is online; the shared reusable key remains for the
   next instance.

The real `.tailscale-authkey` is intentionally ignored by Git. Treat it as a
high-value Tailnet credential: never commit, paste into logs, or distribute it
with a repository archive. Revoke and replace it immediately if exposed.

Useful commands:

```bash
./hermes-spawn.sh list
./hermes-spawn.sh status HOSTNAME
./hermes-spawn.sh setup HOSTNAME
./hermes-spawn.sh chat HOSTNAME
./hermes-spawn.sh shell HOSTNAME
./hermes-spawn.sh logs HOSTNAME
./hermes-spawn.sh credentials HOSTNAME
./hermes-spawn.sh reauth HOSTNAME
./hermes-spawn.sh update HOSTNAME
./hermes-spawn.sh apply-stack HOSTNAME [--restart]
./hermes-spawn.sh stack-status HOSTNAME
./hermes-spawn.sh stop HOSTNAME
```

## Host model stack (deploy.sh)

`deploy.sh` provisions one reliable endpoint on this host that every instance
shares. It is idempotent - re-run it any time something breaks, or run
`./deploy.sh --verify-only` for a non-destructive audit.

What it sets up and repairs:

1. **CCS + CLIProxyAPI** on port `8317`: `npm i -g @kaitranntt/ccs` and
   `ccs cliproxy --latest`.
2. **OAuth** for both upstream providers, with operator guidance and
   skip-already-authed logic: `ccs codex --auth` (ChatGPT Pro, serves
   `gpt-6-astra`) and `ccs claude --auth` (Anthropic, serves `claude-opus-5`
   and the Claude family).
3. **Chutes** as an OpenAI-compatible upstream (`openai-compatibility:` block
   appended to `~/.ccs/cliproxy/config.yaml` with 2-space-indented list items -
   CCS regenerates the config and silently drops unindented hand-written
   lists). Model aliases: `kimi-k3`, `kimi-k2.6`, `glm-5.2`, `deepseek-v3.2`,
   `deepseek-v4-flash-0731`.
4. **One bearer key for the fleet** (`hermes-fleet-...`) in the proxy's
   `api-keys`, stored (mode `0600`, git-ignored) in `.model-endpoint`, which
   also records `MODEL_STACK_BASE_URL=http://<host-tailscale-ip>:8317/v1`.
5. **systemd persistence** (`ccs-cliproxy.service`, oneshot) so the endpoint
   comes back on reboot.
6. Optional registration of the endpoint in `~/.prime/agent/models.json`
   (`--with-prime-models` or answer yes at the prompt).

OAuth accounts over quota return straight `429`s; the endpoint does not swap
models for you. The reliability story lives at the instance layer: Hermes'
own fallback chain (below) walks to the next model.

Non-interactive run: `./deploy.sh --yes` (accepts defaults; answers every
prompt with its default, so OAuth is *verified* but can only be minted
interactively). Force both OAuth logins again with `./deploy.sh --reauth`.

## Fleet defaults applied on spawn

`stack-defaults.yaml` is the single source of truth. `hermes-spawn.sh spawn`
applies it to the new default profile; `./hermes-spawn.sh apply-stack
[instance] [--restart]` (idempotent) applies it to an existing instance
(default + every persona); `./hermes-spawn.sh stack-status [instance]` audits
on disk and exits non-zero on any drift. Re-run `apply-stack` after adding
personas to an instance.

- **Primary model**: `gpt-6-astra` (high) via `custom:ccs-astra`.
- **Fallback chain** (walked on rate-limit/overload/connection errors):
  `claude-opus-5` (max) via `custom:ccs-anthropic` → `kimi-k3` (max) via
  `custom:ccs-kimi` → `deepseek-v4-flash-0731` (max) via `custom:ccs-deepseek`
  → `glm-5.2` (max) via `custom:ccs-glm`.
- **Auxiliaries**: `claude-haiku-4-5-20251001` via `custom:ccs-anthropic` on
  all 9 auxiliary tasks (incl. compression), every persona, with two
  reliability guards baked in: each task carries an explicitly empty
  `reasoning_effort: ''` (omitted on the wire - background lanes never
  inhale the persona's effort, which would otherwise stall small calls
  inside the 600s wrapper) and its own native `fallback_chain`
  (kimi-k3 → deepseek-v4-flash-0731), so an OAuth-account 429 storm
  degrades aux work instead of bricking it.
- **Scheduled jobs**: `cron.model`/`cron.model_provider` are left null, so
  jobs inherit the persona config. A job that needs a specific model for
  optimal performance may pin `model`/`provider` on the job itself; such pins
  are preserved and reported by `stack-status`.
- **YOLO**: `approvals.mode: 'off'` on every persona + default, verified on
  disk. Stray `model.api_key`/`model.api_mode` leftovers from retired
  providers are removed (they would fight the provider entry).
- **Reactions**: off everywhere - YAML (`discord.reactions`,
  `telegram.reactions`, `slack/matrix/mattermost` extras,
  `display.message_reactions`) plus the winning env layer
  (`TELEGRAM_REACTIONS=false`, etc. per-persona `.env`).
- **Expand thinking**: on for every persona (`display.show_reasoning: true`,
  `display.thinking_mode: full`, `display.details_mode: expanded`).
- **Backends**: when an instance has more than one persona the default
  profile's gateway is configured for multiplexing
  (`gateway.multiplex_profiles: true`, `auto_multiplex_migration: true`) and
  fleet capacity is sized from the persona count N:
  `gateway.api_server.max_concurrent_runs = clamp(2N, 10, 32)` and
  `max_live_sessions = min(N+8, 128)`.

The endpoint recorded per instance in `control.env`
(`MODEL_STACK_BASE_URL`/`MODEL_STACK_API_KEY`) is refreshed on every apply.

## Files (model stack)

Instance files live in `instances/HOSTNAME/`. Back up `hermes-data/` to retain
the agent configuration, sessions, memories, skills, and profile state. Back up
`tailscale-state/` only if you also want to retain the Tailnet node identity.

## Network and containment model

There are no published Docker ports. Hermes joins the Tailscale container's
network namespace and listens only on `127.0.0.1` there. Tailscale Serve proxies
the dashboard as private HTTPS on port 443 and the authenticated OpenAI-style
API as private HTTPS on port 8443. Tailscale Serve does not enable Funnel, so
these endpoints are not public internet endpoints.

The generated API key is shown by `credentials`. The dashboard relies on
Tailnet identity and ACLs plus a generated Hermes dashboard login; both the
login and the Hermes Desktop/API connection details are shown by
`credentials`. Restrict access to each node in the Tailscale policy to the
users or device tags that should control that Hermes instance.

For Hermes Desktop, use the `API base` and `API key` printed by
`./hermes-spawn.sh credentials HOSTNAME`. For the browser dashboard, use the
printed `Dashboard`, `Web user`, and `Web pass` values and choose **Sign in
with Username & Password**.

Hermes receives only its instance's `hermes-data/` bind mount. It does not get
the Docker socket, `/root`, the host network, host PID namespace, or privileged
mode. Linux capabilities are dropped except for the small set the official
image needs during startup to switch from its supervisor to the unprivileged
`hermes` user. Tailscale receives only `NET_ADMIN`, `NET_RAW`, `/dev/net/tun`,
and its own state directory. Both services have process, memory, CPU, log-size,
and restart controls.

This is strong whole-process container isolation, not a claim of mathematical
or VM-grade security. Anyone controlling Docker on the host, the Tailnet admin
plane, a configured model/provider credential, or a trusted Hermes plugin is
inside part of the trust boundary. For hostile multi-tenant workloads, put each
pair in its own VM as an additional boundary.

## Operational notes

- The auth-key file is emptied after the first successful Tailscale login;
  persisted node state handles later restarts. If that identity is revoked or
  expires, use `reauth` to enroll it again without disturbing Hermes data.
- Tailscale HTTPS certificates must be enabled for the tailnet. If the first
  Serve attempt prints an enablement URL, follow it and run
  `./hermes-spawn.sh serve HOSTNAME` again.
- `update` pulls the current official image tags. Inspect and pin
  `HERMES_IMAGE` and `TAILSCALE_IMAGE` in an instance's `control.env` if your
  change-control policy requires immutable image digests.
- Do not run two Hermes containers against the same `hermes-data/` directory.
- Hermes is intentionally not given the host Docker socket. Its local terminal
  tools already execute inside the contained Hermes filesystem; mounting the
  socket would effectively grant it host-root-equivalent control.

## Files

- `deploy.sh` — one-endpoint host model stack, interactive and idempotent.
- `stack-defaults.yaml` — fleet default values applied to every instance.
- `lib/apply_stack.py` — merge/audit engine used by apply-stack/stack-status.
- `.model-endpoint` — ignored mode-`0600` fleet endpoint + bearer key.
- `compose.yaml` — hardened two-container template shared by every instance.
- `hermes-spawn.sh` — provisioning and lifecycle interface.

- `.tailscale-authkey.example` — non-secret template for the administrator.
- `.tailscale-authkey` — ignored mode-`0600` reusable enrollment credential.
- `instances/HOSTNAME/control.env` — generated Compose controls (mode `0600`).
- `instances/HOSTNAME/system-info.txt` — host capacity/security snapshot.
- `instances/HOSTNAME/hermes-data/` — all mutable Hermes state.
- `instances/HOSTNAME/tailscale-state/` — persistent Tailscale node state.
