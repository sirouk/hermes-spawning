# Desktop room registry: read-only inspection and safety boundaries

The Desktop Group Chat's **saved server projection** is
`ui_meta['hermes-bots-groups']` in its active profile's `profile.yaml`:

```
ui_meta['hermes-bots-groups'] = {version, updatedAt, rooms{}, deleted{}}
_ui_meta_revisions['hermes-bots-groups'] = <CAS counter>
```

Hosted engine rooms (`hosted_rooms`, `hosted_room_events`) live in a separate
store. Desktop does not use `groups.send`, `groups.log`, or `groups.state` to
render its room messages. A healthy hosted transcript can still be **invisible**
to a Desktop user. Purging or recreating hosted engine rooms does not fix the
Desktop list. A `profile.yaml` inspection proves only the saved projection;
a connected Desktop may have unsynced local state and does not poll for backend
writes. Confirm what the operator actually sees after startup or reconnect.

## Room keys: do not invert their meaning

From Desktop `apps/desktop/src/plugins/hermes-bots/create-dialog.tsx`, new
rooms get `roomId = mintGroupRoomId()` (a **client-minted room identity**).
`group-chat.ts` then keys the `ui_meta` projection with
`groupChatRoomKey()`: `id:<roomId>` when an ID is set, `name:<Display>`
otherwise. Its `pullGroupChatServerState()` merges the projection into the
client's rooms; therefore an ID-keyed projection can render in Desktop.

```
id:<client_room_id>  current Desktop-created room, rendered via ui_meta on pull
name:<Display>      roomId: null; valid, potentially active legacy Desktop room
id:<roomId>        ID key alone says nothing about hosted-engine binding
```

A `name:`/null room may hold the transcript the human is viewing. It is
**not** a defect, even though current new rooms use client-minted IDs. Never
replace its key with `id:<engineRoomId>`, bind it to a hosted-engine ID, or
recreate its empty log: a prior conversion resulted in empty sidebar rooms
while the untouched name-keyed, null-ID room held the operator's real
discussion. A client-minted `roomId` is not a `hosted_rooms.room_id` by
implication. Never infer *live* Desktop visibility from an engine row, an
`id:` key, a successful engine send, or a `profile.yaml` read.

## Tombstone semantics (diagnostic only)

When replaying the Desktop reducer on a **copy** of the saved registry:

```
id:    tombstone is final, regardless of room revision
name:  tombstone hides room only if deletedRevision >= room.revision
       otherwise reducer drops the stale tombstone and keeps the room
```

A stale tombstone does not grant permission to bump it or delete the room.
The apparent room may be a live Desktop-native room, and the client may have
more recent local state. Keep existing `rooms`, `deleted`, logs, members,
revisions, and CAS counters unchanged during diagnosis.

## Member entry shape

```yaml
name: <profile>
handle: <profile>
connectionId: local          # or the remote gateway's id
connectionKind: local        # 'local' | 'remote'
connectionLabel: this device # or the remote host label
sourceScoped: true
```

Inspect recorded members rather than blindly replacing them with today's
profile list; remote members can have a different connection identity.

## Safe diagnostic order

1. Locate the profile and back it up before any separately approved change.
   Read its room keys, `roomId`, `revision`, members, log lengths, tombstones
   and CAS counter without mutating anything.
2. Run `scripts/rebuild_room_registry.py --profile-yaml <path> --verify`.
   This historical filename is now **read-only**. Default mode is read-only
   too. It replays tombstones on a copy, accepts healthy `name:`/null rooms,
   and can compare members with `--personas bot-a,bot-b`. If only `id:` keys
   survive, its `CHECK NEEDED` result is **not** a finding that current
   Desktop-created ID rooms are broken or engine-bound; confirm client state.
   It does not inspect hosted engine state; `--engine` is rejected and
   `--apply` is disabled.
3. Ask the operator to inspect the actual Desktop room after client startup
   or reconnect. A saved projection check alone cannot say what is rendered
   in a currently open app, nor confirm delivery or resolve unsynced local
   state. Never label the client view as stale solely from a server file.
4. For a specific operator-approved edit, obtain the exact room key and
   current client state. Plan a **separate reviewed** change that preserves
   unrelated rooms, logs and tombstones, and accounts for client sync races.
   No registry edit, room creation, deletion, or hosted binding is implemented
   by this skill's diagnostic script.

## Writing is out of scope for this script

The supported gateway RPC for `ui_meta` writes is `profiles.configure` with
`ui_meta_expected_revisions`; the gateway checks a per-key compare-and-swap.
A connected Desktop can also publish its local snapshot at a freshly read
revision, potentially overwriting backend changes. Do not replace this CAS
protocol with blind direct file edits, do not clear a room log, and never
rewrite a null-ID Desktop room as an engine-bound room. Refer to
`hermes-group-chat-delivery/references/desktop-ui-meta-delivery.md` when the
task is to post to the Desktop projection (not to rebuild the roster).
