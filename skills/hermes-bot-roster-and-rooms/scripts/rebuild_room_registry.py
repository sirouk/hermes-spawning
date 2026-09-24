#!/usr/bin/env python
"""Inspect the stored Hermes Desktop Group Chat registry without modifying it.

This was formerly a destructive rebuild tool. Its --apply mode is intentionally
DISABLED: replacing name:<Display>/roomId:null Desktop rooms with hosted engine
IDs can erase their logs and cannot make hosted-engine posts visible in Desktop.
The script never writes profile.yaml, backups, room stores, or the live gateway.

Examples (use a Python environment with PyYAML):
    python scripts/rebuild_room_registry.py --profile-yaml profile.yaml
    python scripts/rebuild_room_registry.py --profile-yaml profile.yaml --verify
    python scripts/rebuild_room_registry.py --profile-yaml profile.yaml --verify --personas bot-a,bot-b

Results describe the saved server projection, NOT what an already-open Desktop
client currently displays. Confirm client visibility with the human separately.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

KEY = "hermes-bots-groups"


def _mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _revision(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def inspect_rooms(groups: dict) -> tuple[dict, list[str]]:
    """Replay tombstones over a copy; do not delete even stale tombstone data."""
    stored = _mapping(groups.get("rooms", {}), "rooms")
    deleted = _mapping(groups.get("deleted", {}), "deleted")
    rooms = {}
    warnings = []
    for key, room in stored.items():
        if not isinstance(key, str):
            raise ValueError("rooms keys must be strings")
        rooms[key] = _mapping(room, f"rooms[{key!r}]")
    for key, drev in deleted.items():
        if not isinstance(key, str):
            raise ValueError("deleted keys must be strings")
        deleted_rev = _revision(drev, f"deleted[{key!r}]")
        if key.startswith("id:"):
            rooms.pop(key, None)  # id tombstones are unconditional
        elif key in rooms:
            room_rev = _revision(rooms[key].get("revision", 0),
                                 f"rooms[{key!r}].revision")
            if deleted_rev >= room_rev:
                rooms.pop(key, None)
            else:
                warnings.append(f"{key}: stale tombstone ({deleted_rev} < "
                                f"room revision {room_rev}); Desktop reducer drops it")
    return rooms, warnings


def verify(profile_yaml: Path, personas: list[str] | None = None) -> int:
    try:
        doc = _mapping(yaml.safe_load(profile_yaml.read_text()), "profile.yaml")
        metadata = _mapping(doc.get("ui_meta", {}), "ui_meta")
        if KEY not in metadata:
            raise ValueError(f"ui_meta[{KEY!r}] is missing; no Desktop room projection to inspect")
        groups = _mapping(metadata[KEY], f"ui_meta[{KEY!r}]")
        rooms, warnings = inspect_rooms(groups)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"cannot inspect Desktop projection: {exc}", file=sys.stderr)
        return 2

    failures = []
    native_rooms = 0
    print(f"Saved Desktop room projection ({profile_yaml}); not live-client visibility:")
    for key, room in sorted(rooms.items()):
        members = room.get("members", [])
        log = room.get("log", [])
        if not isinstance(members, list) or not isinstance(log, list):
            failures.append(f"{key}: members and log must be lists")
            continue
        names = [member.get("name") if isinstance(member, dict) else None
                 for member in members]
        print(f"  {key} name={room.get('name')!r} roomId={room.get('roomId')!r} "
              f"members={names!r} stored_log_entries={len(log)}")
        if key.startswith("name:") and room.get("roomId") is None:
            native_rooms += 1
            print("    Desktop-native shape: roomId:null is healthy; retain its log and members.")
        elif key.startswith("id:"):
            print("    ID-keyed projection: hosted-engine posts do not render in Desktop; "
                  "this is not client-visibility proof.")
            warnings.append(f"{key}: confirm any apparent room/messages in the live client")
        else:
            warnings.append(f"{key}: unexpected room key/roomId pairing; inspect in Desktop")
        if personas is not None and names != personas:
            failures.append(f"{key}: members {names!r} differ from expected {personas!r}")
    for warning in warnings:
        print(f"  WARNING: {warning}")
    if not rooms:
        failures.append("no active rooms in the saved Desktop projection; confirm intended state")
    elif not native_rooms:
        failures.append("no name:/roomId:null Desktop-native room in the saved projection; "
                        "hosted engine room IDs do not establish Desktop visibility")
    if failures:
        print("CHECK NEEDED (saved projection only):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Stored projection check OK. Desktop may have unsynced local state; "
          "ask the operator to confirm what the client displays.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile-yaml", type=Path, default=Path("profile.yaml"))
    ap.add_argument("--personas", help="optional comma-separated expected Desktop member names")
    ap.add_argument("--engine", help="UNSUPPORTED: hosted-engine rooms are not Desktop rooms")
    ap.add_argument("--apply", action="store_true", help="DISABLED: destructive rebuild is unsafe")
    ap.add_argument("--verify", action="store_true", help="read-only saved-projection check")
    args = ap.parse_args(argv)

    if args.apply:
        print("REFUSED: --apply is disabled. Do not convert Desktop name:/roomId:null "
              "rooms to hosted id: rooms or replace their logs. Preserve the "
              "registry, take a backup, and plan any change with the operator "
              "against the live Desktop state and a supported CAS path.", file=sys.stderr)
        return 2
    if args.engine is not None:
        print("REFUSED: --engine cannot repair or verify Desktop visibility; "
              "hosted rooms are a separate, invisible-to-Desktop engine. "
              "Inspect the saved ui_meta projection instead.", file=sys.stderr)
        return 2
    personas = None
    if args.personas is not None:
        personas = [name.strip() for name in args.personas.split(",")]
        if not all(personas) or len(set(personas)) != len(personas):
            print("--personas requires distinct, nonempty names", file=sys.stderr)
            return 2
    return verify(args.profile_yaml, personas)


if __name__ == "__main__":
    raise SystemExit(main())
