# Desktop Group Chat delivery — the client-side path (the one that renders)

Hermes Desktop's Group Chat does **not** use the hosted-room engine. Grep the
plugin and confirm it yourself before doubting this:

```bash
grep -rn "groups\.send\|groups\.log\|groups\.state" apps/desktop/src/plugins/hermes-bots/
# -> zero matches
```

Desktop implements rooms entirely client-side and persists them into the
**default profile's** `ui_meta['hermes-bots-groups']`. A `groups.send` that
returns a clean ACK, allocates a seq, and drives real member turns is still
**invisible in Desktop** — two unrelated systems sharing display names.

Source of truth: `apps/desktop/src/plugins/hermes-bots/` — `group-chat.ts`
(store + sync), `group-rounds.ts` (`sendToGroupChat`), `group-turns.ts`
(`ensureGroupChatSession`, member turns), `group-round-prompt.ts` (prompt).

## Three RPCs, that is the whole contract

| Step | RPC | Notes |
|---|---|---|
| Read projection | `profiles.list {include_sessions:false}` | take profile `default` -> `ui_meta['hermes-bots-groups']`, and `ui_meta_revisions['hermes-bots-groups']` for CAS |
| Write projection | `profiles.configure {name:'default', ui_meta:{...}, ui_meta_expected_revisions:{...}}` | must return `applied.ui_meta === true` |
| Drive a member | `session.create` / `session.resume` + `prompt.submit` | per member profile, hidden session |

## Room keys and entry shape

Current Desktop's `create-dialog.tsx` mints a fresh client `roomId` with
`mintGroupRoomId()`; `group-chat.ts` keys its `ui_meta` projection as
`id:<client_room_id>` and merges that projection into client rooms on pull.
An `id:` key or non-null `roomId` is **not** proof of a hosted-engine binding.
Legacy `name:<Display Name>` rooms with `roomId: null` remain valid and may
be active. Do not "repair" a null ID by replacing it with a hosted-engine ID
or its log. `groups.send` posts to the separate hosted engine and does not
render its transcript in Desktop, whatever the projection's key.

Log entry (exact — `group-chat.ts appendGroupChatEntry`):

```json
{ "id": "<uuid4>", "from": {"kind": "user", "name": "You"},
  "text": "...", "at": 1790061204281, "thread": "t<base36ms>-<rand5>" }
```

Member entries use `{"kind":"member","name":"<profile>","source":"<label>"}`.
`at` is epoch **milliseconds**. Entry identity for merge is `id:<uuid>`.

## Posting formula

1. `read_projection()` -> `(snapshot, cas_revision)`.
2. `room = snapshot['rooms'][room_key]`; mint a thread id if starting one.
3. Append the entry to `room['log']`.
4. Bump `room['revision'] += 1` and set `snapshot['updatedAt'] = now_ms()`.
5. `write_projection(snapshot, expected_revision=cas_revision)`.
6. Re-read and assert the entry is present and the CAS revision advanced.

That records the entry in the backend projection; it does not prove the
connected Desktop rendered it. Member replies are a separate step.

## Making bots actually reply

A "bot in a group chat" is just that profile's own hidden session being fed
the room transcript. Per member:

1. Session title contract: `Group: <roomId or displayName> · <thread>`.
   `session.resume` by that title first; on `4007` fall through to
   `session.create {profile, title, hidden:true, room_plumbing:true,
   follow_profile_config:true}`.
2. Build the prompt with the participation rules + room delta as
   `Name: text` lines (`buildGroupChatTurnPrompt` / `formatGroupChatLine`).
3. `prompt.submit`; poll `session.resume` until an assistant message
   with nonempty `text` appears *after* the submit. `busy: false` alone can
   occur mid-tool-call and does not prove completion. A timed-out or empty
   result is unresolved, never an implied `(pass)`.
4. Append it back as a `member` entry via the posting formula above.

Working implementation: `references/desktop_group_post.py`.

### Two contract details that cost a debug cycle each

- **`session.create` returns TWO ids.** `session_id` is the ephemeral
  **runtime** handle (what `prompt.submit` takes); `stored_session_id` is the
  durable id (what `session.resume` takes). Resuming by the runtime id
  raises `4007` and reads like "the session vanished".
- **`session.resume` returns messages as `{role, text}`, NOT `{role,
  content}`.** Reading `content` yields empty strings and looks exactly like
  "the bot never replied" — when it did. Read `text`, fall back to `content`.

## Timer / cron viability — read this before scheduling

Posting on a timer works. **Live visibility on a timer does not, by default.**

- `pullGroupChatServerState()` has exactly two callers (`plugin.tsx` startup
  and `handleSessionsGatewayTransition`). **Desktop never polls.** An open
  Desktop will not show a backend-written entry until it restarts or the
  gateway connection flips.
- A later Desktop pull may merge entries by key. Backend readback alone does
  not guarantee they will appear: a connected client can also publish a
  competing snapshot. Confirm visibility on the client when it matters.
- **Clobber hazard (read from source, not yet empirically tested):**
  `flushGroupChatServerSync` publishes Desktop's **local** snapshot, CAS'd on
  a freshly-read remote revision. A connected Desktop that has not pulled
  since your write can therefore win the CAS and drop your entry. Treat a
  connected Desktop as a competing writer.
- Practical rule: schedule the post, then **verify by re-reading the
  projection**, and keep `desktop_visibility_verified=false` until a human
  confirms on screen. Do not report a timer post as "delivered to Desktop".
- Rejected `profiles.configure` with `applied.ui_meta: false` can be a
  stale revision, oversized incoming `ui_meta`, or invalid payload. The
  coordinated source patch raises the gateway's
  `len(json.dumps(incoming))` cap from 65,536 to 262,144 characters. This
  counts the full incoming wrapper and any other `ui_meta` keys; the actual
  installed gateway version must be checked. Patched Desktop uses a separate
  192,000-byte conservative envelope, but an old client still budgets 48,000
  bytes and can silently omit rooms. Do **not** assume an operator's Mac has
  the patch. Back up the full client state and projections; upgrade/verify
  gateway first, then Desktop, and retain old-client warnings until confirmed.
  On rollback, stop larger publishes and restore old-client headroom before
  reverting the gateway (incoming writes must fit its old cap). A rejection
  is **not** automatically a CAS conflict. Diagnose payload size and fresh
  projection (including tombstones), then reconcile by exact entry id. Do
  not delete missing rooms, auto-reconcile or blindly replay a write.

## Tombstones and deletion

`snapshot['deleted']` maps a room key to the revision at which it died. A
write that re-adds a key whose tombstone revision is **>=** the room's
revision is dropped as a resurrection. Before posting, confirm the target key
is in `rooms` and not in `deleted` — otherwise the post silently vanishes or,
worse, resurrects a closed room.

When the user says "I closed those rooms", verify it landed: re-read the
projection and check `rooms` / `deleted` / CAS revision actually changed.
`scheduleGroupChatServerSync` early-returns without publishing when the local
room set is empty and `allowEmpty` is false, so a close can legitimately not
have reached the backend yet. Ask rather than guess which room to write.
