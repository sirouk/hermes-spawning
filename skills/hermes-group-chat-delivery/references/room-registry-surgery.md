# Desktop room registry: read-only diagnosis

This file previously advised converting a healthy Desktop `name:<Display>` room
with `roomId: null` into a hosted-engine `id:<roomId>` room and replacing its
log. **Do not use that recipe.** Desktop renders its own `ui_meta` projection;
hosted `groups.*` rooms and their posts are separate and are not displayed by
the shipped Desktop client. The former recipe could erase the Desktop room's
members and transcript without fixing delivery.

Use the canonical, read-only diagnostic:
`../../hermes-bot-roster-and-rooms/references/room-registry-surgery.md`.
The sibling `scripts/rebuild_room_registry.py` now rejects `--apply` and
`--engine`. Preserve all existing rooms, logs, and tombstones until the
operator identifies the intended surface and approves a source-checked repair.
A backend readback is not proof that the connected Desktop has rendered it.
