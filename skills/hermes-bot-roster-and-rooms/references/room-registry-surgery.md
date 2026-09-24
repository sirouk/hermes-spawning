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

From Desktop `apps/desktop/src/plugins/hermes-bots/group-chat.ts`, the key is
`id:<roomId>` when an ID is set and `name:<Display>` otherwise:

```
name:<Display>  roomId: null; normal, healthy Desktop-native room
id:<roomId>    ID-keyed projection, NOT proof of Desktop/hosted engine delivery
```

A `name:`/null room may hold the transcript the human is viewing. It is **not**
a legacy defect. Setting a hosted-engine `roomId`, replacing the name key with
`id:<engineRoomId>`, or recreating its empty log would destroy the wrong
surface. A prior attempted conversion resulted in empty sidebar rooms while
an untouched name-keyed, null-ID room held the operator's real discussion.
Never infer Desktop visibility from an engine row, an `id:` key, a successful
engine send, or a `profile.yaml` read.

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
   and can compare members with `--personas bot-a,bot-b`. It does not inspect
   hosted engine state; `--engine` is rejected, and `--apply` is disabled.
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
