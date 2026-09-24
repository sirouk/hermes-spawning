---
name: hermes-group-chat-delivery
description: "Deliver a post into a Hermes Desktop Group Chat room, scheduled or live, with honest backend-versus-Desktop visibility checks."
version: 3.2.1
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [hermes, group-chat, hosted-room, cron, scheduling, desktop, delivery, idempotency]
---

# Hermes Group Chat Delivery

Deliver a **scheduled** post into a Hermes Desktop Group Chat room so
it lands in the room the human actually watches in Hermes Desktop.
Use this when wiring a cron job, heartbeat, or any automation whose output
belongs in a bot Group Chat — not just when chatting interactively.

The generic failure this skill prevents: **the write path works and posts
exist, but every one was fired by hand, and the scheduled automation that
exists delivers to a DM or a file.** The send path, the scheduler, and the
visible room are three different things; all three must be wired, and none
may be claimed without measured proof.

## Two unrelated systems share the name "Group Chat" — pick the right one FIRST

There are **two complete, independent implementations**. Choosing wrong costs
hours and produces a perfect-looking delivery nobody can see.

| | **Desktop path** (renders) | **Hosted-room engine** (does not) |
|---|---|---|
| Written by | `profiles.configure` -> `ui_meta` | `groups.send` RPC -> room driver |
| Stored in | `profile.yaml` -> `ui_meta['hermes-bots-groups']` | `hosted_room_events` in shared state DB |
| Member turns | `prompt.submit` per profile, `source='tui'` | driver tasks, `source='bot_room'` |
| Room key | `id:<client_room_id>` on current Desktop; legacy `name:<Display Name>` with null ID also valid | `room_id` |
| `roomId` | Client-minted ID on new rooms; `null` on valid legacy rooms; neither implies an engine binding | always set |
| Renders in Desktop | **YES** | **NO** |

**The Desktop plugin contains zero references to `groups.send`, `groups.log`,
or `groups.state`.** Verify in one command before you trust any other claim:

```bash
grep -rn "groups\.send\|groups\.log\|groups\.state" apps/desktop/src/plugins/hermes-bots/
# -> no matches. Desktop's group chat is entirely client-side.
```

So a `groups.send` can return a clean ACK, allocate a seq, drive real member
turns, settle correctly — and be **invisible** to Desktop. Current Desktop's
`create-dialog.tsx` calls `mintGroupRoomId()` for a new room; `group-chat.ts`
projects it as `id:<client_room_id>` and merges that `ui_meta` room into the
client on pull. **An `id:` key and a non-null `roomId` do not by themselves
mean a hosted-engine binding.** On-record failure: a room converted to an
**engine ID** sat at 0 rendered messages while the room the operator watched
was a `name:`-keyed, `roomId: null` room in the same registry.

**`roomId: null` is not a defect to repair.** Valid legacy `name:`/null rooms
can still render, just as current client-ID rooms can. Do not "fix" a null
room by binding it to a hosted-engine ID. Match the exact `ui_meta` room key
and check the actual client view; do not trust a lookup that assumes all
non-null IDs are hosted-engine bindings or all null IDs are broken.

**If the goal is "the human sees it in Desktop": use the Desktop path.**
Full formula, RPCs, entry schema, timer viability, and a verified-working
module: `references/desktop-ui-meta-delivery.md` and
`references/desktop_group_post.py`. The rest of this skill covers the engine
path — correct for engine rooms, and the right choice only when the consumer
is backend automation rather than a human watching Desktop.

The third surface, distinct from both: a per-profile **BotChat DM** (a session
in `profiles/<profile>/state.db`) — a private 1:1, never a room.

Before wiring anything, **measure the real room live**: read
`ui_meta['hermes-bots-groups']['rooms']` in `profile.yaml`; record the room
key, `revision`, `len(log)`, and member roster. For a Desktop room, verify
every saved member's `connectionId` against the operator's affected Desktop
connection registry; the gateway hostname, URL and current connection label
are not the ID. A room with `local` seats cannot pass a remote gateway filter;
a guessed ID creates unreachable ghosts. Use
`hermes-bot-roster-and-rooms` and its read-only `--require-room-connection`
check before scheduling delivery. A server PASS is not a member turn or human
Desktop view. Never act on a stale snapshot — capacity/activity decisions
made from an old revision have been wrong before. Re-measure `revision` and
log length before re-raising any policy question (archival, capacity).

## Delivery contract (how a message reaches the room)

Use the **native room RPC** against the local gateway (default
`127.0.0.1:9119`) over an authenticated WebSocket JSON-RPC session — not a
message channel, not a DB write:

- `groups.capabilities`, `groups.state` — admission checks (driver running,
  exact roster, advertised native features).
- `groups.send` with an exact user payload `{"text": ..., "thread_id": ...}`.
  The server maps the client-supplied ID through `user_event_id`. It returns
  **asynchronous acceptance**, not a visible-post receipt.
- `groups.log` (paged, cursor-based) — the only ground truth for what landed.

Transport rules (from the installed pattern): reuse the existing
credential-safe adapter; never re-implement auth. Authenticate to the
loopback gateway with existing dashboard credentials, request a WS ticket,
and pass it as a **subprotocol** (`hermes-gateway-v1` +
`hermes-gateway-ticket.<ticket>`) so the ticket stays out of URL/access
logs. Set `trust_env=False`, verify identity with an authenticated `/me`
readback, and never log credentials or stack traces.

Native hard limits: **3 serial rounds / 10 member messages per send**. Bot
members only reply when the room driver is running and unblocked; nothing
here needs a Desktop tab open, but the owning backend must stay alive.

## Scope note: conditional_send guards the ENGINE path only

Everything below about `conditional_send` applies to `groups.send`. It does
**not** gate the Desktop `ui_meta` path, which has no such feature and never
consults it. On record: a runtime overlay was built to add `conditional_send`
to unblock "delivery" while the actual invisibility had a different cause
entirely — the wrong surface. Before treating a missing capability as the
blocker, confirm which path the human's room is on.

The Desktop path's concurrency control is the `ui_meta` CAS revision, not
`expected_seq`. It is a real fence, and it is enforced by the gateway.

## The conditional-send interlock (load-bearing — never weaken it)

Before any automated send, require the native feature set to include
`conditional_send` (alongside `idempotent_send`, `actor_identity`,
`typed_events`) and send with a caller-supplied `expected_seq` +
`authority_epoch` precondition.

Why this is not optional: a plain `groups.send` contract carries only
`{event_id, payload}` and the backend allocates `seq` inside its own
transaction. With no expected-seq precondition, **every** check-then-send
has an uncloseable window: a human (or another occurrence) can post between
your log inspection and your append, and your send then silently supersedes
their turn. A second read does not close that window. If the backend does
not advertise `conditional_send`, the only correct result is **BLOCKED /
native_contract_unavailable** — never drop the requirement to make posts
flow, and never "fix" the block by barreling through with a fresher fence.

## Scheduling contract (the part that is always missing)

Code that can post to a room usually exists; the defect is that **nothing
schedules it**. A completion/ACK/capability check is not a schedule.

- A cron job must **own the occurrence caller**. Hand-fired rounds prove the
  write path works and prove nothing else. Audit ownership mechanically
  (e.g. `grep -l "room_cycle|room_schedule" profiles/*/cron/jobs.json`);
  history showing past posts does not prove a schedule exists.
- Wire a **native `no_agent: true` script job**. Point `script` at a
  regular `.py` file inside `profiles/<profile>/scripts/`, with `workdir`
  set as required by the caller. An absolute path is allowed **inside that
  scripts directory**; a relative path also works. Use `script_required`
  as an installation/workflow policy check, **not** as a persisted native
  cron job key. Native Python cron scripts run with the scheduler's
  `sys.executable`; the script shebang does not select the interpreter.
- **Deployment-specific identity checks are not generic Hermes behavior.**
  The optional strict parent-identity wrapper
  (`references/wrapper-template.py`) checks its parent's pid and `/proc`
  start time against a `source='builtin'` ledger
  occurrence, plus `HERMES_CRON_SCHEDULED_AT` and `HERMES_HOME`. If using
  *that wrapper*, keep the cycle as an in-process import: shelling out or
  reparenting defeats its fingerprint, and a real file (not symlink) is its
  policy. Do not attribute that fingerprint to native cron or require it of
  unrelated group room deployments.
- `deliver: bot-chat:<profile>` on a cron job goes to that profile's **DM**,
  not the group; `deliver: local` posts nothing at all. Neither is a room
  cycle. (For a `no_agent` script job whose whole effect is the room send,
  `deliver: local` on the job itself is fine — the transcript is not the
  product. What matters is `failure_deliver` below.)
- **Failure delivery matters**: a scheduled job that dies must say so where
  the human reads. Never leave `failure_deliver` on `local` for agent jobs
  that owe the human a report.
- **Set `cron.script_timeout_seconds` above the caller's full round budget**
  plus RPC/readback margin via the supported config path. Native default is
  3600 seconds; a smaller configured value can kill a bounded caller. With
  the helper's default 420 seconds per member, budget for **420 × roster
  size** (or pass `round_budget_s` to set an explicit shared total). Refuse
  activation if the script timeout cannot cover the total.
- Completions can be real and still **suppress delivery**: some schedulers
  gate on `bool(deliver_content.strip())`, so a run that wrote real analysis
  but diverged from its final response (or expected an opt-out marker such
  as `[SILENT]`) completes "ok" with nothing delivered. When a job reports
  ok-but-suppressed, suspect that divergence, not the send path.

## The Desktop formula (copy this — it is the whole contract)

Verified working on Hermes 0.21.4 / desktop `v2026.9.14`. Runnable module:
`references/desktop_group_post.py`. Prose and rationale:
`references/desktop-ui-meta-delivery.md`.

**Post a message — three RPCs against the local gateway, no client involved:**

1. **Read** `profiles.list {include_sessions:false}` -> profile `default` ->
   `ui_meta['hermes-bots-groups']` and
   `ui_meta_revisions['hermes-bots-groups']` (the CAS fence).
2. **Append** to `rooms[<key>]['log']`, then bump:

   ```json
   { "id": "<uuid4>", "from": {"kind": "user", "name": "You"},
     "text": "...", "at": 1790061204281, "thread": "tm<base36ms>-<rand5>" }
   ```

   `at` is epoch **milliseconds**. Members use
   `{"kind":"member","name":"<profile>","source":"<label>"}`. Merge identity
   is `id:<uuid>`. Also set `room['revision'] += 1` and
   `snapshot['updatedAt'] = now_ms()`.
3. **Write** `profiles.configure {name:'default', ui_meta:{...},
   ui_meta_expected_revisions:{'hermes-bots-groups': <revision from step 1>}}`.
   Require `applied.ui_meta === true`, then **re-read and assert the entry is
   present and the revision advanced** — an ACK is not a write. `applied:
   false` alone is ambiguous: stale revision, oversized incoming payload, or
   validation failure. Do not infer a CAS conflict, auto-rebase, or log raw
   gateway response content; inspect the fresh projection and payload size.
   The observed ~64KiB limit counts characters in `json.dumps(incoming)`,
   **not** bytes of the entire persisted `ui_meta` projection.

**Make members actually reply** (a "bot in a room" is just that profile's own
hidden session being fed the transcript):

1. `session.resume {session_id: "Group: <roomId or name> · <thread>", profile,
   omit_messages:true}`; on `4007` fall through to
   `session.create {profile, title, hidden:true, room_plumbing:true,
   follow_profile_config:true}`.
2. `prompt.submit` with the participation rules + room delta as `Name: text`
   lines (`buildGroupChatTurnPrompt` / `formatGroupChatLine`; rules travel in
   the payload, NOT in SOUL.md, so any bot can join with no migration).
3. Poll `session.resume` until a NEW, nonempty assistant message is
   observed after submission (while not busy), or the monotonic deadline
   expires. `busy:false` alone is **not** completion; a tool-heavy turn can
   report non-busy in mid-work.
4. Append the observed answer as a `member` entry via the posting formula.
   An **observed literal** `(pass)` means "nothing to add" and is not appended.
   Timeout or an empty assistant message is **unresolved**, never `(pass)`;
   inspect the session by title and report an incomplete round.

### The two traps that cost a debug cycle each

- **`session.create` returns TWO ids.** `session_id` is the ephemeral
  **runtime** handle — what `prompt.submit` takes. `stored_session_id` is the
  **durable** id — what `session.resume` takes. Resuming by the runtime id
  raises `4007` and reads like "the session vanished".
- **`session.resume` returns messages as `{role, text}`, NOT `{role,
  content}`.** Reading `content` yields empty strings and looks exactly like
  "the bot never replied" when it did reply. Read `text`, fall back to
  `content`, and handle a list-of-parts shape.

### The harvest trap that cost a full cycle (the worst one)

A driver can post a real kickoff, make every member produce a real,
full-length answer in its own session, and still record `replies: 0` —
leaving the room containing only the kickoff. In one observed cycle, five
of six members answered (1.4k–3.8k chars each, one making a real
mission decision with key inputs re-verified), but the result still said zero
and the log had the kickoff alone. Three stacked causes, all
in the driver, none in the members:

1. **Crossed ids.** `session.resume` on a title returns its own ids; passing
   the resume-path `session_id` to `prompt.submit` and the durable path the
   other way starves the poll or submits to a handle that accepts the prompt
   but whose growth you never read. Name the two handles for their ROLES —
   `runtime` accepts submits, `stored` is what resume reads — and never
   re-derive one from the other.
2. **Baseline on a moved stored id.** A member recovering a long session under
   a runtimenew runtime handle, or a resume that serves a moved/copy history,
   can present a different stored id. If the poll baseline ("messages now vs
   before") keys off that stored id, the count never grows and every member
   reads as done-with-no-reply. Key the baseline off the session TITLE — its
   history is stable id-agnostic.
3. **Non-busy ≠ done.** A session whose last visible event is tool calls
   (no assistant text yet) reports non-busy mid-turn or during prompt prep.
   On the second-5s tick this reads as "finished, nothing to say" — which is
   exactly how the recon lane above returned 0 assistant msgs. When history
   grew but carries no assistant text yet, keep polling; treat only deadline
   collapse as 'no reply'.

Fix shape (in `references/desktop_group_post.py`): `run_member_turn(submit_id,
resume_id, ...)` with role-named params, a title-keyed
`run_member_turn_by_title` wrapper, and `keep polling unless a new assistant
text exists or the monotonic deadline has collapsed`. The members did the
work; the driver just has to read it correctly. A serial round sets ONE shared
monotonic deadline and recalculates each member's slice as
`remaining_time / remaining_members` each iteration, with a bounded floor.
A quick speaker leaves extra time for the tail; a floor never extends the
shared deadline. `drive_round(..., per_member_budget_s=420)` keeps the old
**total** default of `420 × N` for N members (the argument names that baseline,
not a hard limit on each individual turn). Use `round_budget_s=<total seconds>`
for an explicit tighter round. Set the cron script timeout above that full
budget plus RPC margin. Unresolved
members raise `RoundIncomplete` with the already-posted entries; never call an
unresolved round COMPLETE or replay it wholesale.

Recovery is a manual harvest, not a re-send: re-read each member's session by
title, pull the last assistant message, and append it as a `member` entry under
the same thread (the kickoff that already landed), ordered to match the
kickoff's stated speaker order. Never re-submit the round just to populate the
log — that forks a second discussion over the one the desk already had.

## Can this run on a timer? (Desktop path)

**Posting on a timer: yes.** The write is three plain RPCs against the local
gateway with no Desktop client involved. Wire it exactly like any other room
cron job (`no_agent` script pointing to an in-profile file). A specific
occurrence wrapper may impose extra identity checks; native Hermes cron does
not impose the optional wrapper's parent-pid fingerprint. The CAS revision fences
writes: read revision N, write at N, then read back the entry. A rejected
write is not necessarily a CAS conflict; it could also be an oversized or
invalid incoming payload.

**Live visibility on a timer: no, not by default.** From the source,
`pullGroupChatServerState()` has exactly two callers — app startup in
`plugin.tsx` and `handleSessionsGatewayTransition`. **Desktop never polls.**
An already-open Desktop will not show a backend-written entry until it
restarts or its gateway connection flips. A backend entry can merge into a
client's view on its next pull (`id:<uuid>` entry identity), but do not promise
that a connected client cannot overwrite or drop it in the meantime.

Consequences for any scheduled design:

- Report evidence in levels: **submit/ACK** (request accepted; not proof of a
  write), **backend readback** (entry id present and CAS revision advanced;
  durable at that check, not proof of a client view), and **Desktop verified**
  (a person saw the right room/thread, attributed replies, and a composer
  follow-up worked). Never collapse one level into the next. A timer post
  *may* become visible on the next Desktop pull, but another connected
  writer can also change the backend projection before then.
- Keep `desktop_visibility_verified=false` for scheduled results until the
  live Desktop check succeeds. The backend cannot observe the client.
- **A connected Desktop is a competing writer.** `flushGroupChatServerSync`
  publishes the client's local snapshot CAS'd on a freshly-read revision, so a
  client that has not pulled since your write can win the CAS and drop your
  entry. (Read from source; not yet empirically tested — verify before
  relying on it either way.) Lowest-risk scheduling is when no client is
  connected; after any client transition, re-read and reconcile the exact
  entry id before considering a manual re-append.
- On `applied.ui_meta: false`, **stop**. It is not proof of stale CAS.
  Check the current revision, tombstones, incoming payload size, and any
  safely classified error detail. Preserve the original entry id when
  reconciling uncertain outcomes; do not blindly retry/rebase or resurrect
  a room that the user deleted.
- **Check tombstones before every scheduled post.** `snapshot['deleted']` maps
  a room key to the revision at which it died; writing a key whose tombstone
  revision is >= the room's revision is dropped as a resurrection. A timer
  that posts into a room the user closed is a correctness bug, not a nuisance.

If the requirement is genuinely "a human sees new room traffic without
touching the app", the honest answer is that no supported backend-only path
delivers it today: Desktop pulls on startup and gateway transition only.

## Occurrence identity, idempotency, and the journal

- Freeze **one immutable request per scheduled occurrence**, keyed by the
  **native scheduled instant from the ledger** — never invent one from wall
  clock. It carries: real `job_id`, the exact scheduled instant, the cycle
  window, a deadline, and the payload.
- Derive event IDs **deterministically** from `room + job_id + scheduled
  instant + phase`. Retries reuse the identical request/event ID. **Never
  allocate a new ID on retry.** Identical input appended twice must dedupe
  natively; changed input under the same occurrence key must conflict, not
  fork a second discussion.
- Keep a **SQLite occurrence journal** (keyed `job_id:scheduled_instant`,
  `PRAGMA synchronous=FULL`) that seals the immutable request, the last
  status/result, and a pre-send **in-flight marker**. Add an exclusive
  `flock` so overlapping caller processes refuse each other. Terminal rows
  short-circuit and replay their result; a prior *unresolved* occurrence
  blocks starting a new one.
- **Crash-after-send fence**: on restart, if the in-flight marker exists,
  reconcile by readback; if the send is absent from the room, do not rebase
  and resend — return BLOCKED with "uncertain send requires human recovery."
  On any transport timeout / missing readback, retry only the IDENTICAL
  request (INDETERMINATE).
- A replacement native execution must not impersonate the original owner:
  if the native context changed, refuse (manual recovery), never adopt.
- One invocation = one bounded step (at most one send), bounded polls
  (e.g. ≤210 × ≤10 s), deadline-enforced. PENDING is normal: an
  asynchronous cycle cannot complete in one invocation — debate settles,
  then the decree goes into the same thread on a later tick. Do not schedule
  recursive new occurrences just to poll the same one.
- A deadline blocks further sends/polling admission but does **not** cancel
  already-admitted native turns, and never auto-stop a room (a room-wide
  stop may interrupt the human). The parent owns the stop decision.

## Release gating (safe to install, opt-in to post)

Install the wiring in **default read-only** mode so it is safe on the grid
day one, and require two independent flips before any send:

1. An env opt-in (e.g. `<PREFIX>_APPLY=1`) that the wrapper reads.
2. A release/gate JSON (`frontend_verified: true`,
   `scheduled_release_approved: true`, exact `room_id`) **re-read before
   every send** — not just at process start — so revocation between checks
   takes effect. Bind acceptance to evidence (file path + SHA256, expiry),
   never a bare boolean, and provide **no `--force`** flag.

Before each send the guarded caller also re-verifies: native owner context
unchanged, the sealed request still matches current truth (research/content
changed -> conflict, not send), the deadline not elapsed, and the gate still
open. Any failure is terminal for that send, never a retry-through.

## Status conventions and honesty

Suggested exit codes (read-only by default; a single apply switch is the
only native-write path):

| Status | Meaning | Exit |
|---|---|---|
| READY | admission preview only — **not completion** | 0 |
| COMPLETE | native transcript checks passed (settled, committed, attributed) | 0 |
| PENDING | not due / discussion not settled / room busy / poll budget | 2 |
| BLOCKED | driver down, roster drift, feature missing, human intervened, gate closed | 3 |
| EXPIRED | deadline passed (native work may still be running) | 4 |
| INDETERMINATE | send outcome uncertain — retry the **identical** request | 5 |
| error | exception | 1 |

- **A send ACK is not a durable write or visibility.** An engine result
  needs exact `groups.log` readback; a Desktop `ui_meta` result needs entry-id
  plus CAS-revision readback; neither proves a live client rendered it.
  Carry `desktop_visibility_verified=false` until a human verifies in the
  actual Desktop room. Completion is a transcript/projection check, not
  decision quality or authority.
- For **engine** rooms, verify COMPLETE by reading back the **exact events**
  in `groups.log`:
  the scheduled-origin message, distinct committed/attributed member replies
  (`message.member` whose `turn.settled` commits its `message_event_id`),
  native settlement (`room.activity` settled/bounded by the authority
  gateway), and no log gaps/cursor jumps within the scan budget.
- **Never claim "visible in Desktop" from a backend receipt.** Real failure
  on record: a writer that posted fine and was still not visible — earned
  the operator verdict "Nope, this is not visible." Only a live connected
  client check plus a human follow-up through the composer closes that loop.
- **Archive before trim.** If room logs must shrink to fit an incoming
  `ui_meta` write, persist removed entries in an id-keyed, locked, fsynced
  append-only archive *before* submitting a trimmed snapshot. If the write
  rejects, the original live projection remains and the archived copy is
  harmless. Do not silently drop the only copy of a discussion to make a
  payload fit; journal the occurrence before send and reconcile uncertain
  outcomes by readback before any retry.
- Exceptions must surface as a type name in structured JSON, never a stack
  trace into the room or cron transcript. Retry a sealed engine occurrence
  only under its own idempotency/readback rules. For an ambiguous Desktop
  rejection or unresolved member turn, first reconcile by exact entry id and
  member session title; never blindly resubmit or allocate new entry ids.

## A job can be `[active]` and never fire

A schedule dict missing `kind` (e.g. `{"expr": "20 3,7,11,15,19,23 * * *"}`
written by hand or by a script) makes `compute_next_run` return `None`:
`hermes cron list` shows `Next run: ?`, the job never arms, **nothing ever
runs, and no error is raised anywhere**. The room simply stays empty, which
looks identical to a correctly-gated read-only install.

- Diagnose with `hermes cron doctor --profile <p>` — it reports `active job
  has no next_run_at` directly. Run it after ANY hand-written job edit.
- Repair through the CLI so the schedule gets normalized to
  `{kind: cron, expr, display}`:
  `hermes cron edit <job_id> --profile <p> --schedule "<expr>"`.
- **`hermes cron edit` needs explicit `--profile <p>` (or `HERMES_HOME`
  targeting that profile)**; `HERMES_PROFILE` in the environment is NOT
  honored and an absent profile selection can answer `Job not found`.
- Verify `next_run_at` is populated afterwards — the edit is the fix only if
  the timestamp appears.
- `timeout_seconds` on the job is **not** read for `no_agent` script jobs;
  only `cron.script_timeout_seconds` / `HERMES_CRON_SCRIPT_TIMEOUT` (default
  3600s) bounds them. Setting a small per-job value is a no-op that reads
  like protection.

## Optional strict parent-identity wrapper: manual cron differs

**This applies only to `references/wrapper-template.py`-style strict identity
policy, not every native Hermes job.** `cron run` and `cron tick` can produce
a `source='direct'` execution row. That wrapper requires exactly one
`source='builtin'` row, so it refuses a direct call
(`Unique native review job required`) — expected under that policy, not a
broken script. If a tick is killed (it can block for the caller's full poll
budget), a row can remain `status='running'` with a dead pid and block the
next scheduled occurrence. Check and recover only for deployments that use
this wrapper/policy.

Clear a stranded row through the supported recovery path, pointing
`HERMES_HOME` at the PROFILE directory (the ledger is per-profile; running it
against `/opt/data` silently recovers 0 rows):

```bash
HERMES_HOME=/opt/data/profiles/<profile> python -c \
  "import sys; sys.path.insert(0,'/opt/hermes'); \
   from cron import executions as ex; print(ex.recover_interrupted_executions())"
```

## Pitfalls (rule + why)

- **Do not "fix" hidden room sessions.** Room dispatch creates every room
  session `hidden=1` by design; unhiding them is not a fix and breaks the
  registry's assumptions.
- **Do not trust discovery tools that match only on `roomId`.** A valid
  legacy Desktop room can carry `roomId: null`, while a current Desktop room
  carries a client-minted ID unrelated to `hosted_rooms`. Match the exact
  `ui_meta` key and inspect the client for Desktop delivery. Use
  `groups.state`/live rosters only for the separate hosted-engine path.
- **Verify a user-reported room closure before writing anywhere.** When the
  user says "I closed those rooms", re-read the projection and check that
  `rooms`/`deleted`/the CAS revision actually changed.
  `scheduleGroupChatServerSync` early-returns *without publishing* when the
  local room set is empty and `allowEmpty` is false (only an explicit
  final-room disband sets it), so a close can legitimately not have reached
  the backend. If the backend still shows every room active, ask which room
  to target rather than guessing — posting into a tombstoned room resurrects
  it, which is exactly what the revision-gated tombstones exist to prevent.
- **If using the strict parent-identity wrapper, do not diagnose by firing it
  manually.** Its parent-identity check demands a builtin parent-owned
  execution and rejects `source=direct`; that is wrapper policy, not a native
  cron invariant.
- **Never weaken the `conditional_send` gate to unblock posting.** The
  block is the safety property; the fix is a backend that supports
  conditional admission, not a loosened client.
- **The optional wrapper's strict identity policy** requires a real script
  file and an in-process import of the cycle module; do not generalize its parent-pid
  fingerprint as native Hermes cron behavior. Python cron uses the scheduler's
  `sys.executable`, not the script's shebang.
- **Do not put credentials or unverified "latest report" claims into the
  payload.** The room adapter validates schema; it cannot prove provenance.
  Bind content to verified artifacts upstream (exact execution IDs + bytes),
  and label stale evidence as stale.
- **Log scanning is bounded** (e.g. 100 pages x 500 events). Hitting the
  budget must BLOCK, never treat a truncated transcript as absence.
- **Re-measure before re-asking.** Room `revision`/log length move fast; a
  capacity or archival question raised from a stale snapshot has already
  been wrong once. Fresh measurement is cheap; a wrong policy question is
  not.

## Wiring checklist

0. **Decide which system you are on before anything else.** If a human must
   see it in Desktop, use the Desktop `ui_meta` path
   (`references/desktop-ui-meta-delivery.md`) — NOT `groups.send`. Confirm
   with the one-line grep in the surfaces section above. The Desktop sidebar
   list is built from `ui_meta['hermes-bots-groups']` in `profile.yaml`,
   **not** from the engine's `hosted_rooms` table — purging `hosted_rooms`
   leaves the sidebar unchanged. Both current `id:<client_room_id>` rooms
   and valid legacy `name:`/null rooms can render from `ui_meta`; the key
   does not prove a hosted-engine binding.
   Diagnose roster and Desktop registry state read-only with the sibling
   `hermes-bot-roster-and-rooms` skill. Its registry-surgery procedure is
   disabled; do not nuke, recreate, or mutate rooms as a diagnostic step.
1. Identify the real room: live-read `ui_meta['hermes-bots-groups']['rooms']`
   in `profile.yaml`; record key, `revision`, roster, member handles.
2. **Engine (`groups.send`) path only:** verify `groups.capabilities` /
   `groups.state`: driver running, roster exact, and the required feature set
   — including **`conditional_send`** — is advertised. If not, report BLOCKED.
   **Desktop (`ui_meta`) path:** use the Desktop formula and its read-back,
   attribution, and visibility checks. Do not require engine capabilities for
   a Desktop room; they do not govern this path.
3. Write a profile-local `.py` script in `profiles/<profile>/scripts/`.
   The optional `references/wrapper-template.py` adds its own parent-pid
   identity check and thus requires a real file with an in-process import;
   ordinary native cron does not require that extra identity pattern.
4. Wire ONE native `no_agent` script job (`script_required` is a workflow
   policy check, not a native persisted key) on the desired grid;
   confirm it lands in the profile's `cron/jobs.json`; set
   `cron.script_timeout_seconds` above the caller's budget; set
   `failure_deliver` to a surface the human reads.
5. Ship default read-only; opt-in env flag + release JSON gate sends.
6. Dry-run `--plan` offline, then a supervised read-only run (READY/PENDING),
   then — with gate open — one supervised apply occurrence.
7. For the **engine** path, read back the exact events in `groups.log`;
   confirm settlement, attribution, no gaps. For the **Desktop** path,
   `profiles.list` must show the exact entry id and an advanced CAS revision;
   inspect member sessions by title for unresolved turns.
8. Only then verify on the live connected Desktop: right room row, right
   thread, attributed replies, and a human composer reply gets a response.
   Report `desktop_visibility_verified` truthfully until step 8 passes.
