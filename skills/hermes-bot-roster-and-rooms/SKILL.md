---
name: hermes-bot-roster-and-rooms
description: Diagnose the Desktop bot list and Group Chat room projection without replacing Desktop-native rooms.
version: 0.3.1
author: Hermes
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Hermes, Bots, GroupChat, Profiles, Desktop, Rooms]
    related_skills: [hermes-agent-fleet-ops, hermes-group-chat-delivery]
---

# Hermes Bot Roster and Group Chat Rooms

Diagnose retired bots and Group Chat rooms without converting Desktop-native
rooms to hosted-engine IDs. The read-only registry script never changes rooms.
This skill distinguishes the profile roster, Desktop room projection, and
hosted-room engine. It does NOT cover scheduled posting into a room (see
`hermes-group-chat-delivery`) or fleet-wide config sweeps (see
`hermes-agent-fleet-ops`).

## When to Use

- "I still see the old bots" / "it's a mix of old and new"
- "the old group rooms are still there" / "this room has stuff from the old bots"
- "you were supposed to make new rooms" / one room looks missing
- Retiring personas after a roster change and the UI disagrees with disk
- Don't use for: delivering a scheduled post into a room; per-job cron repair.

## Three stores, three different fixes

The sidebar is assembled from stores that drift independently. Diagnose which
one is wrong FIRST — the fix for one does nothing for the others.

| Sidebar section | Source of truth | Fix with |
|---|---|---|
| Bot rows (fleet section) | profile dirs under `/opt/data/profiles/` | `hermes profile delete <name> -y` |
| GROUP CHATS server projection (rooms and synced logs) | `ui_meta['hermes-bots-groups']` in the active profile's `profile.yaml` | read-only diagnostic (Procedure 2); confirm live Desktop with operator |
| Hosted-room engine rooms (invisible to Desktop) | `hosted_rooms` in `<home>/shared-state.db` | `gateway.hosted_rooms` API |

**Desktop's Group Chat is entirely client-side.** The plugin
(`apps/desktop/src/plugins/hermes-bots/`) contains ZERO references to
`groups.send`, `groups.log`, or `groups.state` — it stores rooms in `ui_meta`,
appends messages with `room.log.push`, and drives member replies with
`prompt.submit` into each member profile's own hidden session. The
`hosted_rooms` engine is a separate system Desktop never reads. Confirm in one
command before trusting anything else:

```bash
grep -rn "groups\.send\|groups\.log\|groups\.state" apps/desktop/src/plugins/hermes-bots/
# -> no matches
```

So an engine room can hold a full, correctly-settled transcript and render
**nothing**. Posting into it is not a delivery path to the human.

A "mix of old and new bots" is ALWAYS the profile list — room membership lives
in the room stores and never renders in that section. Purging rooms to fix a
stale bot roster is wasted work.

A room still listed after a hosted database cleanup may be in Desktop local
state and/or the `profile.yaml` projection. The Desktop does not read
`hosted_rooms` to build its list. A saved projection read alone cannot prove
what an already-open Desktop client displays.

## Prerequisites

- For the read-only registry script, use an interpreter with `yaml` (PyYAML),
  such as `/opt/hermes/.venv/bin/python`. No engine import is required.
- The installed runtime is `/opt/hermes`, which may differ from a working
  checkout. Verify before trusting any source you read:
  `/opt/hermes/.venv/bin/python -c "import gateway; print(gateway.__file__)"`
- Read access to the active profile’s `profile.yaml`. The registry script is
  read-only and does not require write access.

## Quick Reference

```
hermes profile list
hermes profile delete <name> -y
hermes profile create <name>              # recreate a stub to unblock boot
hermes profile purge-identity <name>      # after ProfileIdentitySettlementPending
```

Hosted-room engine state is separate. Do not use `gateway.hosted_rooms` or an
engine room ID to repair a Desktop Group Chat room.

Registry keys in `profile.yaml`: `ui_meta['hermes-bots-groups']` =
`{version, updatedAt, rooms{}, deleted{}}`; CAS counter at
`_ui_meta_revisions['hermes-bots-groups']`.

## Procedure 1 — retire bots from the sidebar

1. Compare intent to disk: `search_files(pattern='*', target='files',
   path='/opt/data/profiles')`. Every row in the user's screenshot must resolve
   to a directory or a retired name; a truncated UI label reads as whatever you
   expect, so enumerate rather than guess.
2. Grep surviving `config.yaml` and `cron/jobs.json` for each retired name, then
   FILTER the hits — a retired `foo` matches every mention of its successor
   `foo-v2` as a substring. Print surrounding context and confirm each hit is a
   collision before concluding there is no dependency.
3. Delete through the lifecycle command via `terminal`:
   `hermes profile delete <name> -y`. It disables the s6 service, stops the
   gateway holding SQLite handles, tombstones the name, notifies the
   multiplexer, and purges routing identity. Manual `rm` skips all of it.
4. Sweep for orphaned supervisors: for each `pgrep -f 's6-supervise gateway-'`
   pid, read `/proc/<pid>/cmdline` and kill any whose `/run/service/<svc>` is
   gone. Check `ps -o pid,user` first — root-owned supervisors cannot be
   signalled by the agent UID, are idle without a service dir, and clear on the
   next restart. Say that plainly instead of retrying.
5. Completion criterion: `hermes profile list` equals the intended roster plus
   `default`.

## Procedure 2 — inspect Desktop rooms without editing them

Read `references/room-registry-surgery.md` before considering any room change.
Current Desktop creates rooms with a client-minted `roomId` and projects them
under `id:<client_room_id>`; the client can render these via `ui_meta`. A
`name:<Display>` key with `roomId: null` is also a valid, potentially active
legacy Desktop room; do not replace it with a hosted-engine ID. Tombstones
for `name:` keys are revision-gated, and a stale Desktop client can be a
competing writer.

1. Read the active profile’s `profile.yaml`; record room keys, revision,
   `roomId`, members, log lengths, and existing tombstones. Run
   `scripts/rebuild_room_registry.py --profile-yaml <path> --verify` to replay
   the reducer on a copy, not to rebuild. Optionally pass `--personas` for
   an exact expected-members check. The default mode is equally read-only.
2. Distinguish the hosted engine from Desktop. Hosted logs and `groups.send`
   do **not** make Desktop posts visible. The script refuses `--engine`.
   An `id:` key alone does not identify a hosted-engine binding: current
   Desktop creates `id:<client_room_id>` rooms. On record, a previous
   conversion of a `name:`/null room to a **hosted-engine ID** left empty
   Desktop rooms while the untouched room was the one the operator used.
3. Never automate conversion, deletion, tombstone rewrites, log clearing, or
   member replacement based on this check. The script refuses `--apply` even
   with `--verify`. If the operator requests a specific change, first obtain
   the exact target key and current client state, back up the source, and
   design a separate reviewed CAS-controlled change that preserves unrelated
   rooms, logs, tombstones and revisions. This skill ships **no** apply path.
4. Confirm with the operator which room the Desktop client displays after
   reconnect/startup. A read of `profile.yaml` is evidence only about the
   stored projection, not the live Desktop view. Do not claim visible or fixed
   from the script’s success alone.

## Procedure 3 — confirm what the human sees

The Desktop pulls on app startup or gateway transition, not by polling. A
`profile.yaml` read confirms the saved server projection only; it cannot
confirm live client visibility, local unsynced room edits or delivery. Ask the
operator to refresh/reconnect, inspect the target room and report the actual
view. If a screenshot conflicts with your projection check, do not dismiss it
as cache or claim a repair: reconcile the client, profile, and separate engine
states.

## Pitfalls

- **`name:` tombstones are revision-gated.** The reducer keeps a tombstone only
  when `deletedRevision >= room.revision`; otherwise it DROPS the tombstone and
  the room survives the next sync. `id:` tombstones are final and unconditional.
- **Purging `hosted_rooms` does not change the sidebar.** Different store.
- **Do not use direct DB surgery as a Desktop room repair.** Engine database
  changes are unrelated to Desktop projection visibility; running gateways
  may also hold in-memory state. No engine mutation is in this procedure.
- **Deleting the profile named in the container CMD halts the container.** `-p`
  resolution fails, the CMD exits nonzero, and `rc.init` passes that to
  `haltwith`. Check what the CMD process is really doing first
  (`ps -eo pid,ppid,cmd --ppid <pid>`): under multiplex it has usually already
  handed off and become `sleep infinity`, so the pinned profile does no work and
  deleting it costs nothing until the next restart. Surface the CMD edit as the
  user's decision, tell them not to restart until it lands, and give them
  `hermes profile create <name>` as the unblock.
- **`conditional_send` gates the ENGINE path only — it is not why a Desktop
  room looks empty.** It guards `groups.send`; Desktop's rooms never call it.
  On record: a runtime overlay was built to add `conditional_send` to "unblock
  delivery" when the real cause was writing to the wrong surface entirely.
  Before treating a missing capability as the blocker, establish which system
  the user's room is on. To post where a human will see it, write the `ui_meta`
  projection (skill `hermes-group-chat-delivery`,
  `references/desktop-ui-meta-delivery.md`).
- **Desktop pulls the registry on startup and gateway transition only — it
  never polls.** `pullGroupChatServerState()` has exactly two callers. A
  backend write to `ui_meta` is durable immediately but will not appear in an
  already-open Desktop until it reconnects or restarts. Never report a backend
  write as "visible" — and expect the user to tell you it is not.
- **A connected Desktop is a competing writer for `ui_meta`.**
  `flushGroupChatServerSync` publishes the client's local snapshot CAS'd on a
  freshly-read revision, so a client that has not pulled since your write can
  win the CAS and drop your entry. Prefer writing when no client is connected,
  and always read back to confirm the entry survived.
- **Verify a user-reported room closure before acting on it.**
  `scheduleGroupChatServerSync` early-returns *without publishing* when the
  local room set is empty and `allowEmpty` is false (only an explicit
  final-room disband sets it). So "I closed those rooms" can legitimately leave
  every room still active in `profile.yaml`. Re-read the projection and check
  `rooms`/`deleted`/the CAS revision actually moved; if they did not, ask which
  room to target rather than guessing.
- **`/opt/hermes` is a sealed image layer — patching it is not a repair path.**
  Root-copying files there survives only until the next s6/container restart,
  when the image layer resets them (`docker/stage2-hook.sh` documents the seal:
  it exists specifically to stop an agent session self-modifying runtime code).
  A capability verified right after a root copy will silently revert. To load
  patched runtime code, build an overlay tree and put it on `PYTHONPATH`.
- **PYTHONPATH alone cannot override the runtime.** `hermes_cli/main.py` calls
  `_startup_fast.ensure_project_root_on_path()`, which REMOVES the repo root
  from anywhere in `sys.path` and force-inserts it at `sys.path[0]` — ahead of
  every PYTHONPATH entry. `project_root_str()` derives that root from
  `hermes_cli`'s own location, so an overlay must contain its **own copy of
  `hermes_cli`** to win; symlink every other top-level package back to
  `/opt/hermes` so the overlay cannot drift. Verify by asserting the served
  capability, never by importing the module in a separate process — the
  gateway is its own process and an import test proves nothing about it.
- **A stale `.pth` in user-site breaks site init for EVERY interpreter run.**
  A leftover `conditional_send.pth` raised during `site` import, which killed
  the editable-install finder before PYTHONPATH was consulted and produced
  misleading "module not found / not patched" results across unrelated probes.
  Remove experiment `.pth` files as soon as the experiment ends.
- **Dashboard auth: `.env` beats `config.yaml`.** `HERMES_DASHBOARD_BASIC_AUTH_*`
  in `$HERMES_HOME/.env` overrides `dashboard.basic_auth` in `config.yaml`.
  Reading the username/password from `config.yaml` yields a guaranteed 401 that
  looks like a broken gateway. Resolve credentials the way the provider does.
- **Job prompts go stale about authority.** A prompt asserting a fixed posture
  ("the desk is DRAFT") contradicts reality the moment the mode changes. Make
  prompts DERIVE posture from the file they already read, and scope report-only
  language to the lane's ROLE so it stays true under any mode.
- **An `unknown` cron execution after you kill a gateway is honest, not a bug.**
  The scheduler records that the owner exited before a durable terminal state.
  Check whether the lane could have had side effects before treating it as loss.

## Verification

```
/opt/hermes/.venv/bin/python scripts/rebuild_room_registry.py --verify
```

Replays tombstones over a copy and prints the saved projection. A healthy
legacy `name:`/null room passes; hosted-engine rooms are neither read nor
required. **Caveat:** the read-only script still reports `CHECK NEEDED` when
only `id:` keys survive, even though current Desktop creates valid
`id:<client_room_id>` rooms. Treat that result as a request for client
confirmation, not proof the room is broken or engine-bound. If `--personas`
is set, a member mismatch fails the check. `--apply` and `--engine` fail
closed without writing. Pair it with `hermes profile list` for the bot rows,
then ask the operator to confirm the room in Desktop; this script cannot
attest to the currently displayed client view.
