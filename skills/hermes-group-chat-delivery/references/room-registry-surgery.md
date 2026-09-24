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
