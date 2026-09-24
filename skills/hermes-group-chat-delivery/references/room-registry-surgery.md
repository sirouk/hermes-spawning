# Desktop room registry: read-only diagnosis

This file previously advised converting a healthy Desktop `name:<Display>` room
with `roomId: null` into a hosted-engine `id:<roomId>` room and replacing its
log. **Do not use that recipe.** Current Desktop also creates rooms with a
client-minted `roomId` and projects them under `id:<client_room_id>` in
`ui_meta`; ID-keyed client rooms can render on pull. An `id:` key alone does
not mean a hosted-engine binding. Legacy `name:`/null rooms remain valid.
Hosted `groups.*` posts are separate and are not displayed by the shipped
Desktop client. The former recipe could erase the Desktop room's members
and transcript without fixing delivery.

Use the canonical, read-only diagnostic:
`../../hermes-bot-roster-and-rooms/references/room-registry-surgery.md`.
The sibling `scripts/rebuild_room_registry.py` now rejects `--apply` and
`--engine`. Preserve all existing rooms, logs, and tombstones until the
operator identifies the intended surface and approves a source-checked repair.
A backend readback is not proof that the connected Desktop has rendered it.

## Seat connection ids are never derived from a hostname

Room members carry a `connectionId`. It is a **Desktop connection-registry id**
minted from the connection LABEL the operator typed (slug: lowercase,
non-alphanumeric runs to `-`, max 48 chars, `-2`/`-3` on collision). A rename
keeps the original id, and the id can never be reconstructed from a gateway
hostname, URL, or tailnet name. Its authoritative registry is on the
operator's client (`connections.json` / the Desktop connection list). A
server projection can echo a saved seat id, but cannot prove that id still
belongs to the affected Desktop's live gateway connection.

Desktop keeps a room under a gateway filter only when some seat's
`connectionId` equals that gateway's selected id
(`roster-sections.tsx` `filterBotsByGateway`, `groupMatchesRosterFilters`).
A guessed id therefore produces a room that is visible under `all`, hidden
under its own gateway, and seated entirely with ghost members no live roster
row matches, so no turn can be routed to them. A misleading near-match to a
gateway name is not proof of identity. A second room may hold `local` seats:
that room cannot pass a *remote* gateway filter either, but it might be an
intentional local room. Confirm its owning Desktop before changing it.

Repair through the Desktop member picker (Manage members) whenever possible:
the client writes the correct descriptors, bumps the room revision and
publishes to every gateway, so no id is transcribed by hand. **Picker cap trap:**
with six stale seats pre-checked, the six live bots appear beside identically
named ghosts but are disabled at the six-member limit. Do not tell the human
to distinguish identical names or immediately select the disabled rows. First
uncheck all checked ghosts, then pick live roster rows, or use the narrow
server-side clear below. Save clears removed seats' sessions, watermarks and
holds; back up the client and projections before removal.

If the correct id is unavailable, deletion of the **known-wrong** seats may
still be possible without inventing a replacement id. A separately approved,
backed-up recovery can clear *only* the bad `members` arrays in the named
rooms (keeping keys, `roomId`, log and unrelated state intact), then let
Desktop's live picker seat real bots and publish their real ids. The rooms
can temporarily disappear under a gateway filter while empty; open them
under All to reseat. Precondition: verify every removed seat is wrong and
that no live member turn/session is being erased; inventory **every** default-
profile projection and obtain fresh revisions before writing. Use
`profiles.configure` with `ui_meta_expected_revisions`, not a direct
`profile.yaml` edit. Do not guess a far-future revision, alter other rooms,
or blindly retry a CAS conflict. Read each projection back and require the
new room revision to win over stale client copies, then check Desktop filter,
all seats, and one member reply. A gateway readback alone is not client proof.
**Never wipe a container to fix client-synced room members.**

A server-side reseat requires the operator-supplied exact id AND the merge
rules below, because Desktop reconciles per room by revision
(`group-chat.ts` `mergeRemoteGroupChatSnapshotIntoRooms`,
`mergeGroupChatSyncSnapshots`):

- remote revision **>** local: members are cleared and replaced by the
  projection (the only outcome that removes stale seats);
- remote revision **==** local: members are **unioned** by
  `connectionId::name`, so old ghost seats come back beside the new ones;
- remote revision **<** local: the client's members win and overwrite the edit.

Desktop publishes every local room to **every** reachable default-profile
gateway, so a stale copy on another gateway can reintroduce old seats. Any
server-side reseat must therefore write the same members and the same higher
revision to every projection that holds the room, and be verified by re-reading
each one. Keep the room key, `roomId`, log, unrelated rooms and tombstones
unchanged, and use `profiles.configure` with `ui_meta_expected_revisions`.
