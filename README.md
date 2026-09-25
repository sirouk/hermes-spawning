# Hermes Spawning

`hermes-spawning` creates independent, persistent pairs of
[Nous Research Hermes Agent](https://github.com/NousResearch/hermes-agent) and
[Tailscale](https://tailscale.com/docs/features/containers/docker). Each pair
has its own identity, state, resource limits, credentials, and Docker Compose
project.

## Quick start

```bash
cd /path/to/hermes-spawning
./deploy.sh                 # one-time per host: the model endpoint
./hermes-spawn.sh spawn     # create an instance (fleet defaults are applied)
```

### Acting on every instance at once

Put `all` (or `-a`) where the instance name goes:

```bash
./hermes-spawn.sh restart all          # restart the whole fleet
./hermes-spawn.sh status all
./hermes-spawn.sh apply-stack all -y
./hermes-spawn.sh webui-ensure all
./hermes-spawn.sh credentials all
```

Instances run one after another, each with its own validation and lock. One
failure does not stop the rest; the command exits 1 if any instance failed.
Interactive commands (`chat`, `shell`, `logs`, `setup`, `config`, `reauth`)
refuse `all` because they need a dedicated terminal.

### Compression timeouts are a fleet standard

`stack-defaults.yaml` sets the context-compression budget for every instance:

```yaml
compression:
  threshold: 0.25
  context_total_ceiling_seconds: 2400
  context_timeout_seconds: 600
```

Compression reads the whole context and then writes a summary, so its budget
depends on how fast the summary endpoint is. The upstream default of 600s
suits fast cloud models. A local GGUF endpoint needs much more: a ~141k-token
session measured well past 600s. When the ceiling is hit, Hermes logs
`reached its total ceiling ... continuing without compression` and then blocks
retries for 300s while the session keeps growing.

Every key under `compression:` is applied as-is, so raising the ceiling fleet
wide is a one-line change here plus `./hermes-spawn.sh apply-stack all -y`.

### Preserving an instance's model settings

Set `STACK_MANAGED=no` in `instances/<name>/control.env` to preserve its model
stack, including endpoint/provider settings. This is not a hosting-policy
opt-out: `apply-stack` and `stack-status` still apply and audit the common
approvals, fleet-skill discovery, timeout, display, and gateway policy.

### Fleet skills: teach every spawn the same capabilities

Skills that every instance should inherit live in this repo under `skills/`.
Each skill is a directory with a `SKILL.md` (plus optional `references/`),
the native Hermes skill format. They are installed into the dedicated
`hermes-data/fleet-skills/` directory. Every profile discovers them through
`skills.external_dirs: [/opt/data/fleet-skills]`; existing external directories
are preserved. Root and persona `skills/` directories remain agent-created
local state. They are not copied or exposed as the shared fleet collection.

```bash
./hermes-spawn.sh install-skills HOSTNAME   # push to one instance
./hermes-spawn.sh install-skills all         # push to the whole fleet
```

`spawn` installs the fleet skills into the new instance automatically, so a
fresh spawn starts with the same capabilities as the rest of the fleet.
Spawning cannot discover the future Desktop client's connection ID: after
adding a gateway in Desktop, seat rooms from its live member picker or read
the operator's exact registry ID, then run the room-seat doctor check below
and confirm client rendering plus a member reply. A hostname-shaped ID is
not evidence, and `spawn`/`install-skills` do not create or qualify rooms.
`install-skills` is idempotent (unchanged skills are skipped). The repo is
canonical for named shared skills; per-instance edits to those same names must
be kept in a separate local skill directory or archived before replacement.
The installer stages and byte-verifies a complete new generation before a
same-filesystem atomic directory switch. The previous complete tree is retained
privately for rollback. An atomic path switch still cannot make a sequence of
separate file reads an all-or-nothing snapshot: schedule a maintenance/idle
window for live multi-file skill readers, then verify each persona's native
skill resolution after the rollout. `apply-stack` updates skill discovery
policy but does **not** distribute modified skill files: use `install-skills`
on each instance or `install-skills all` after a repo skill change.

Currently shipped:

- `skills/hermes-group-chat-delivery` — how to deliver a *scheduled* post
  into a Hermes Group Chat (multi-bot room) so it renders in Hermes
  Desktop. It encodes the measured delivery contract: only the UI room
  registry in `profile.yaml` (`ui_meta['hermes-bots-groups']['rooms']`) is
  the room the human watches; cron `deliver: bot-chat:<profile>` lands in a
  DM and `local` posts nothing; the room caller must run in-process from a
  profile-local Python wrapper (a subprocess loses the scheduler parent
  identity the room RPC authenticates against); event IDs are derived
  deterministically from room + job + scheduled instant + phase so retries
  are idempotent; and a send ACK is never "visible in Desktop" without an
  explicit client check. A cron job must own the cycle — hand-fired rounds
  are not a schedule.

- `skills/fleet-organism-design` — doctrine for shaping a multi-persona fleet
  into one organism: how responsibility, authority, and learning are arranged
  so the fleet improves from its own results. Covers the fractal
  Observe/Orient/Decide/Act loop, the anti-artifact standard, which native
  surface owns each kind of state, the observable work state machine,
  independent nowcasts before collective review, separating decision from
  authorization from execution from verification, group-relative (GRPO-inspired)
  comparison for prose and config, retros, and the living versioned operating
  map. `references/fleet-seed.md` carries the founding charter verbatim and is
  the authority when the two disagree. Use it to build out a whole team from
  scratch; it is the design layer, not a repair manual.

- `skills/fleet-convergence-learning` — an append-only, queryable experience
  ledger and retry guard. It stores bounded outcome episodes and source
  locators, retrieves only a few condition-matched patterns, preserves
  contradictions, and prevents an unchanged causal failure from being tried
  blindly again. It is memory for decisions, not a transcript or report store.

- `skills/hermes-bot-roster-and-rooms` — diagnose the Desktop Bot roster
  and Group Chat registry after a fleet change. The sidebar comes from
  profile folders and Desktop's `profile.yaml` `ui_meta` registry. The
  hosted-room engine is separate and invisible to that client. A Desktop
  `name:` room with `roomId: null` can be healthy; rewriting it into an
  `id:` engine room is **not** a visibility repair. Inspect and back up the
  actual target before any registry surgery; use client-visible readback
  rather than trusting a backend write or a script dry run. **Room-seat rule:**
  obtain the exact connection id from the affected Desktop, never from a
  hostname or gateway label; check each room with the read-only
  `lib/fleet_doctor.py --instance-dir instances/<fleet>
  --verified-connection-id <desktop-id>
  --require-room-connection 'id:<room-id>=<desktop-id>'` option.
  A saved-seat PASS is not proof of Desktop visibility or a member reply.
  If wrong seats fill the six-seat picker, follow the backed-up, narrow
  ghost-seat recovery in the skill before reseating through Desktop; never
  wipe a container to repair a client-synced room.

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
./hermes-spawn.sh add-persona HOSTNAME researcher
./hermes-spawn.sh preflight HOSTNAME --json
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
   `gpt-6-sol`) and `ccs claude --auth` (Anthropic, serves `claude-opus-5-5`
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
applies it as a mandatory provisioning step, not an optional prompt.
`./hermes-spawn.sh apply-stack [instance] [--restart]` applies it to an existing
instance (default + every persona). `./hermes-spawn.sh stack-status [instance]`
audits on disk: exit 0 means aligned, 1 means verified drift, and 2 means an
operational/input error. A clean disk audit alone is not runtime verification.
Use `add-persona` below for later personas, or re-run `apply-stack` after
manual native profile creation.

- **Primary model**: `claude-opus-5-5` (max) via `custom:ccs-anthropic`.
- **Fallback chain** (walked on rate-limit/overload/connection errors):
  `gpt-6-sol` (max) via `custom:ccs-astra` → `kimi-k3` (max) via
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
- **YOLO approvals**: all six keys below are applied to default and every
  persona, including model-pinned instances. This removes ordinary approval
  prompts and recoverable approval/Tirith checks. Explicit `approvals.deny`
  rules and the `sudo -S` credential-piping floor remain. Hermes' hardline floor
  still applies to local/host-access terminal paths; its native isolated-backend
  fast path has different handling. Running Hermes in a Docker container does
  not itself select that isolated terminal backend. Approval settings grant no
  missing credentials, account permissions, network access, or MCP sign-in.
  Disk settings are not proof that every execution path works.
  For managed model stacks, stray `model.api_key`/`model.api_mode` leftovers
  from retired providers are removed because they conflict with provider entries.

  ```yaml
  approvals:
    mode: 'off'
    cron_mode: approve
    single_query_mode: approve
    unattended_mode: approve
    mcp_reload_confirm: false
    destructive_slash_confirm: false
  ```
- **Reactions**: off everywhere - YAML (`discord.reactions`,
  `telegram.reactions`, `slack/matrix/mattermost` extras,
  `display.message_reactions`) plus the winning env layer
  (`TELEGRAM_REACTIONS=false`, etc. per-persona `.env`).
- **Expand thinking**: on for every persona (`display.show_reasoning: true`,
  `display.thinking_mode: full`, `display.details_mode: expanded`).
- **Backends**: the launch profile is configured for multiplexing from its
  first startup (`gateway.multiplex_profiles: true`,
  `auto_multiplex_migration: true`), even with one founder. Capacity uses
  `N = max(6, persona count including default)`:
  `gateway.api_server.max_concurrent_runs = clamp(2N, 10, 32)` and
  `max_live_sessions = min(N+8, 128)`. One through six profiles therefore share
  the same target of 12 concurrent runs and 14 live sessions. A seventh raises
  the disk target to 14/15; profile hot-serving is not a claim of live capacity
  resizing. Plan any required gateway restart around active sessions.

For model-managed instances, the endpoint recorded in `control.env`
(`MODEL_STACK_BASE_URL`/`MODEL_STACK_API_KEY`) is refreshed on apply.
Model-pinned instances retain their endpoint settings.

### Adding a clean persona

```bash
./hermes-spawn.sh add-persona HOSTNAME researcher
```

The instance must already be running. This uses native `hermes profile create`
without cloning, sets an explicit profile-local UI registry boundary, applies
the fleet defaults, and audits configuration and isolation. Native gateway
notification must confirm that the new persona is served; it does not restart
the existing crew. An unsupported or disabled gateway fails this step rather
than claiming success. The created profile remains; enable multiplexing and
schedule a safe gateway restart when existing sessions are idle.
It also checks fleet skills through native discovery and `skill_view`. It does
not switch the instance's launch profile or copy another persona's rooms,
memory, sessions, credentials, or local skills. This is not the full
scheduled-runtime preflight or a completed crew: creating profiles is not
proof of independent role work, room participation, handoffs, or delivery.

### Formation before mission work

`skills/fleet-organism-design/references/fleet-seed.md` is the founding charter.
Use it as the first message, then study the mission only enough to design the
crew and resolve necessary operator decisions. **FORMING** lasts until an
accepted operating map and a bounded, observed coordination cycle show that
each role can actually do its part. A founder must not fill a missing role by
doing that role's work. Nor may it treat domain research, datasets, algorithms,
tools, or publication as "unaffected work" while a room, handoff, permission,
or persona is blocked. Continue only unaffected *formation* tasks and place
the precise external blocker in Holding. Formation tests the real script and
room path; profiles, boards, and a send ACK alone are not acceptance.

`preflight` is a runtime/configuration gate, **not** formation acceptance.
Crew formation is never inferred from a gateway's six-persona capacity, a set
of profile folders, or a founder-written status card. Form the crew before
mission buildout. A solo instance remains a valid, separate use case. The
repo cannot prevent a Hermes shell with mission credentials from doing domain
work merely by stating this rule: enforce consequential boundaries with
operator-owned credentials and the actual tool/dispatch path where needed.

Native distributions were evaluated for clean named-profile installation.
They provide installation templates, not live inheritance for later native
profile creation or existing profiles. This launcher therefore reconciles
shared policy into each profile rather than treating a distribution as an
inheritance layer. If a profile is created outside `add-persona`, apply and
verify its policy before use.

## Read-only fleet diagnostics and formation scope

```bash
./hermes-spawn.sh fleet-doctor all --json
./hermes-spawn.sh formation-status all
```

`fleet-doctor` reads profile-local job manifests, scripts, and Desktop registry
metadata. It fails on proven unarmed active schedules, invalid script paths,
legacy `handoff/` directories, or enabled jobs that direct an internal report,
cycle-file, close-file, checkpoint, retro, or handoff-artifact workflow. A real
mission deliverable must declare its path and stable operator-request locator in
the job's `durable_output` configuration. It does **not** start jobs, open live
SQLite databases, contact the Desktop client, or verify actual delivery.
Warnings and `UNVERIFIED` items remain open: a clean disk report does not mean
the fleet works. It does not infer spending from job count or pretend
unknown-effect executions are resolved.
For `desktop_room_capacity`, the doctor estimates the coordinated source-policy
Desktop **192,000 conservative bytes** and gateway **262,144 Python JSON
characters** (the gateway counts the full incoming `ui_meta` object, including
its key wrapper). It also warns when the saved projection approaches the old
Desktop **48,000-byte** limit or exceeds the old gateway **65,536-character**
limit. A `PASS` is only a disk estimate, not proof of an installed client or
gateway version, a successful write, or a rendered room. Even the source patch
at `patches/hermes-desktop-group-projection-192k.patch` is **not proof that any
operator's Desktop, including a Mac client, has been upgraded**. The gateway
can receive other `ui_meta` keys in a write, so a one-key estimate is not a
full payload guarantee.

Before increasing any room projection, back up the owner's full local Desktop
state and every gateway projection. Coordinate writers across gateways; do not
trim unrelated logs, delete missing rooms, or auto-reconcile. Stage the policy:
upgrade and verify the gateway's new limit **first**; then upgrade and verify
the actual Desktop client's new limit. Until the affected client is verified,
treat old-client warnings as active and keep projections within its old budget.
If rolling back, stop larger publishes, restore/verify a client that stays
within the old 48,000-byte limit, then roll back the gateway only after incoming
writes fit its old 65,536-character cap. Preserve room history and revisions;
reconcile any conflict with the owner rather than replaying blind writes.
Native `hermes cron doctor` can provide further version-specific findings; it
also cannot prove that a human saw a room post.

`formation-status` is separate from `preflight`. An absent host-side
`instances/HOSTNAME/formation-acceptance.json` is **unknown**, not solo or
qualified. An explicit private `0600` receipt may declare only
`{"schema":1,"instance":"HOSTNAME","mode":"solo"}` or `mode: "crew"`.
Multiple named profiles refute a solo declaration. Crew status remains
**pending** even if a founder, a receipt, or a board says it passed: this
version cannot independently verify a six-role client-visible formation cycle
and deliberately never emits `formation_qualified: true`. A missing receipt
for a multi-profile crew is flagged, not reclassified as a healthy solo node.
This is a read-only diagnostic, **not a security or execution gate**. It
cannot stop direct Hermes shell work or authorise mission activation. `spawn`
records the wizard's explicit `solo`/`crew` selection in this host-side
receipt before starting Compose; `add-persona` records or upgrades to `crew`
before native profile creation. Neither action accepts a crew. The file stays
outside the agent data bind mount. An existing private, enriched receipt is
never overwritten; downgrade from crew to solo is refused. Existing jobs are
never silently stopped by this command.

An independent, stricter snapshot check is available to the human operator:

```bash
python3 -B lib/formation_admission.py --instance-dir instances/HOSTNAME --scope mission-buildout --json
```

It **denies** unless the operator privately creates one schema-v2 `0600`
`formation-admission.json` ledger row bound to the current
`hermes-data/fleet-runtime.yaml`. The runtime contract must describe exactly six
personas, native Kanban/Group Chat surfaces, the OODA state machine, independent
fan-out/fan-in, bounded convergence recall, and the no-internal-artifacts rule.
Admission records native Kanban, room, schedule, stop/recovery, and convergence
event locators rather than role reports, screenshots, copied evidence, or hash
trees; the exact schema is documented in the script. The check reads current
profiles, shared skills, boards, rooms, schedules, and artifact-culture guards,
but the event locators and people remain operator attestations. It neither
contacts a live Desktop client nor authenticates a human or gates Hermes shell,
cron, gateway, proxy, or agent tools. Keep consequential resources behind the
real operator-controlled boundary and do not call a backend ACK client-visible.

## Recoverable artifact-light cognition reset

`scripts/reset-fleet-cognition.py` is the stopped-only migration from legacy
fleet cognition and paperwork to the current doctrine. Dry-run is the default;
`--apply` moves the exact selected state into one private host-side backup,
installs the repository fleet skills, writes the canonical Fleet Seed SOUL core
to every existing root and named profile, and leaves every schedule empty and
the fleet in `FORMING`. It preserves credentials, connection identity,
configuration, profile metadata, Tailscale state, and mission/source trees. It
never restarts a fleet.

```bash
python3 -B scripts/reset-fleet-cognition.py --all --summary
python3 -B scripts/reset-fleet-cognition.py --all --apply --summary \
  --backup-dir /absolute/private/backup/path
```

The full marker-delimited SOUL core is the safe source of truth. A dense
machine projection is admissible only with the source-bound, independently
checked directive-recovery certificate defined by the fleet design skill; a
missing or stale certificate falls back to the full core. Check or synchronize
the canonical core without touching role-specific text with:

```bash
python3 -B lib/sync_soul_core.py --instance-dir instances/HOSTNAME \
  --seed skills/fleet-organism-design/references/fleet-seed.md --json
python3 -B lib/sync_soul_core.py --instance-dir instances/HOSTNAME \
  --seed skills/fleet-organism-design/references/fleet-seed.md --apply
```

## Read-only readiness and active verification

```bash
./hermes-spawn.sh preflight HOSTNAME --json
```

Preflight reads policy, profile isolation, fleet-skill files, room registries,
MCP configuration, and saved runtime evidence. It hashes the installed Hermes
source through Docker without importing Hermes. It does not run model turns,
connect MCP clients, run jobs, restart services, or write instance state.
Exit 0 means its checks passed, 1 means not ready, and 2 means a read/input error.

A disk audit is not enough. Each persona needs fresh evidence from the separate,
**state-writing** verifier. Use a disposable qualification instance first, then
run on the target only with approval for the resulting state changes:

```bash
python3 scripts/verify-fleet-runtime.py \
  --instance-dir instances/HOSTNAME --allow-state-writes
./hermes-spawn.sh preflight HOSTNAME --json
```

The verifier requires the repository's Python dependencies, including PyYAML,
and a running instance. It runs real model turns and temporary native cron
qualification. These can consume model quota and change sessions, logs, memory,
and plugin state. Temporary-job cleanup does not undo those effects. It checks
foreground terminal stdout/exit, scheduled `execute_code`, harmless but
approval-sensitive shell calls through cron, single-query and API paths, and
native fleet-skill discovery/read. Success evidence is scoped to these tested
calls and their execution context/working directory, not every possible
operation. Future project-local skills can take precedence in native discovery;
this does not certify skill resolution in arbitrary project sessions.

Evidence is stored per persona under `hermes-data/fleet-preflight/`. Preflight
requires it to match current config, environment, defaults, skills, and installed
runtime hashes; its default maximum age is 24 hours. Missing, stale, failed, or
mismatched evidence fails readiness. This is a trusted local operator record,
not a signed attestation or a claim that this checkout has passed live testing.

MCP configuration or token presence is **not verified sign-in**. Preflight does
not test token validity or remote permissions. A readable Desktop global room
registry is **not proof of profile-local room ownership or visible delivery**;
an explicit empty local `ui_meta: {}` is valid isolation, not a working room.
Room ownership, delivery, formation, authorization, and all other gates in
`skills/fleet-organism-design/references/fleet-seed.md` still apply. Do not
start recurring schedules merely because configuration or runtime checks pass.

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

## Operating lessons from live fleets

These were earned the hard way and are non-negotiable doctrine:

1. A `--resume` conversational turn that prints "Another Hermes
process is using this session" never becomes free: the CLI re-checks for ~30 minutes and then refuses the turn. Abort the driver when the banner first
appears, free the session, then relaunch — do not queue a second driver.
2. `docker exec` chat processes survive client disconnect. Killing the
host-side client does not stop the turn inside the container. Check with
`docker exec <container> ps -eo pid,args`; kill leftovers with
`docker exec --privileged -u 0 <container> kill -9 <pids>`.
3. Drive formation mutations as small, atomic CLI calls (one board, one
card, one job) and verify mutation by reading raw store bytes — not by the
echo of the mutating CLI, nor by `hermes cron list` alone.
4. Let conversational turns interpret and report; never delegate unbounded
"go form everything" mutations to them. A turn that ends analysis-complete
but mutation-empty twice in a row is a stall: decompose it.

`scripts/fleet-turn.py` encodes rules 1, 2, and 4 (fast abort on the busy
banner, leftover-process refuse/kill, stdin prompt piping because
`--query-file` resolves inside the container). The full doctrine lives in
`skills/fleet-organism-design/SKILL.md` → "Driving formation turns".

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
- `lib/fleet_preflight.py` — read-only policy and runtime-evidence checker.
- `scripts/verify-fleet-runtime.py` — opt-in, state-writing runtime qualification.
- `.model-endpoint` — ignored mode-`0600` fleet endpoint + bearer key.
- `compose.yaml` — hardened two-container template shared by every instance.
- `hermes-spawn.sh` — provisioning and lifecycle interface.

- `.tailscale-authkey.example` — non-secret template for the administrator.
- `.tailscale-authkey` — ignored mode-`0600` reusable enrollment credential.
- `instances/HOSTNAME/control.env` — generated Compose controls (mode `0600`).
- `instances/HOSTNAME/system-info.txt` — host capacity/security snapshot.
- `instances/HOSTNAME/hermes-data/` — all mutable Hermes state.
- `instances/HOSTNAME/tailscale-state/` — persistent Tailscale node state.


## Web UI, timeouts, and fleet supervisor (ships by default)

Every spawned instance also gets:

* **hermes-webui chat UI** (pinned to `webui.ref` in `stack-defaults.yaml`,
  github.com/nesquena/hermes-webui) vendored per instance, loopback-bound
  inside the instance netns, pointed at that instance's gateway API, and served
  tailnet-only at `https://<node>.<tailnet>.ts.net/webui` **and** at its own
  origin `https://<node>.<tailnet>.ts.net:8788/` (`webui.serve_port`). Manage
  it with `./hermes-spawn.sh webui-ensure <inst>` / `webui-status <inst>`.

  **Why two routes.** The Hermex iOS client
  (github.com/uzairansaruzi/hermex) normalises the server URL you type with
  `AuthManager.normalizedServerURL`, which does `components.path = ""` — it
  **throws the path away** and keeps only scheme/host/port. It then calls
  `/health`, `/api/auth/status` and `/api/auth/login` on that origin. Against a
  `/webui` sub-path mount those calls land on the Hermes dashboard instead and
  return `302` / `401 no_cookie`, so the app can never connect. The port-origin
  route is the shape the app can actually use; `/webui` stays for browsers.
  Point the app at `https://<node>.<tailnet>.ts.net:8788` and sign in with the
  webui password (`./hermes-spawn.sh credentials <inst>`).

  Sharing one hostname is safe: the dashboard sets `hermes_session_at`/`_rt`
  cookies and the webui sets `hermes_session`, so the two logins never clobber
  each other (cookies ignore ports, so the distinct names matter).
* **Fleet timeouts** (`stack-defaults.yaml` `timeouts.env`): `HERMES_AGENT_TIMEOUT=14400`
  and `HERMES_AGENT_TIMEOUT_WARNING=3600` land on every persona `.env` via
  `apply-stack`. The former is hermes' gateway inactivity kill — 30 min stock is
  too aggressive for long research/compaction turns; we ship 4 h (+ 1 h pre-kill
  warning). `0` disables the guard entirely; we deliberately keep a backstop.
* **Fleet supervisor**: `systemd/hermes-fleet-supervisor.service` runs
  `scripts/fleet-supervisor.sh` every 30 s. For each running instance it first
  audits fleet policy. Exit 1 (verified drift) triggers `apply-stack -y --restart`;
  operational errors are logged and do not trigger a stack apply. This also
  catches later native profiles. Model opt-outs still receive common hosting
  policy. The loop then restarts the webui daemon if it died and re-adds missing
  tailscale serve routes (root -> :9119 dashboard, /webui -> :8787, and
  :8788 -> :8787 for Hermex; add-only — never resets routes). Install/inspect it with
  `./hermes-spawn.sh supervisor install|enable|disable|status`. The spawn flow
  offers to install it (default yes); on systemd-less hosts run
  `scripts/fleet-supervisor.sh` as a keep-alive task instead.

### Heavy spawns (many-persona instances)

Heavy instances ride the same lanes; give them headroom via control.env knobs and
the compose template env knobs (`pids_limit`, `mem_limit`, `mem_reservation`,
`cpus`, `shm_size`, `HERMES_STOP_GRACE_PERIOD` via `HERMES_STOP_GRACE_PERIOD`,
default `45 s`). Recommended heavy tier on a 32 vCPU / 62 GiB host:
`HERMES_CPUS=12`, `HERMES_MEMORY=24g`, `HERMES_MEMORY_RESERVATION=4g`,
`HERMES_SHM_SIZE=4g`, `HERMES_PIDS_LIMIT=8192`, `HERMES_STOP_GRACE_PERIOD=90s`,
plus the generalized fleet timeouts above. Long research/compaction
turns live entirely in the fleet timeouts (`HERMES_AGENT_TIMEOUT=14400`,
`HERMES_AGENT_TIMEOUT_WARNING=3600`).

### Single-gateway topology (many-persona instances)

The fleet runs **one gateway per instance**: the s6 `gateway-default` slot
owns the responsive routes (API server on 8642, dashboard, session multiplex);
extra per-profile gateway slots stay registered but DOWN (desired_state
stopped). This is hermes' own boot contract (`container_boot.py` reconcile):
slots always re-register, but only `desired_state: running` autostarts, and the
merge persists across container restarts. `hermes gateway stop|-p <p> gateway
stop` sets the durable desired_state; stop hangs are settled by the container
owner (SIGKILL by the container's hermes uid — container root is cap-dropped).
Rules:

- default home gateway = the multiplexing root gateway (owns :8642 API platform)
- per-profile gateway slots stay registered but DOWN (desired_state stopped)
- boot reconcile re-registers slots but only desired_state `running` starts
- the webui gateway backend key is always the default home API_SERVER_KEY
