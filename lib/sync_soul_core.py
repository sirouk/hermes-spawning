#!/usr/bin/env python3
"""Idempotently place the Fleet Seed's canonical SOUL core in every persona."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import tempfile


START = "<!-- FLEET_SOUL_CORE:START -->"
END = "<!-- FLEET_SOUL_CORE:END -->"


class SyncError(ValueError):
    pass


def extract(seed: Path) -> str:
    try:
        value = seed.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SyncError(f"cannot read seed: {seed}") from exc
    if value.count(START) != 1 or value.count(END) != 1:
        raise SyncError("seed must contain exactly one SOUL core marker pair")
    start = value.index(START)
    end = value.index(END, start) + len(END)
    block = value[start:end]
    if (len(block) > 40_000 or "## PURPOSEFUL CONFIDENCE" not in block
            or "FRACTAL OODA" not in block):
        raise SyncError("seed SOUL core is missing or unexpectedly large")
    return block


def profile_dirs(instance: Path) -> list[Path]:
    home = instance / "hermes-data"
    if not home.is_dir() or not (home / "config.yaml").is_file():
        raise SyncError(f"Hermes data/config missing: {home}")
    result = [home]
    named = home / "profiles"
    if named.is_dir():
        result.extend(sorted(path for path in named.iterdir()
                             if path.is_dir() and not path.is_symlink()
                             and (path / "config.yaml").is_file()))
    return result


def merged(current: str, core: str) -> str:
    starts, ends = current.count(START), current.count(END)
    if (starts, ends) == (0, 0):
        return core + ("\n\n" + current.lstrip() if current.strip() else "\n")
    if (starts, ends) != (1, 1):
        raise SyncError("SOUL has an incomplete or duplicate canonical marker")
    start = current.index(START)
    end = current.index(END, start) + len(END)
    return current[:start] + core + current[end:]


def atomic_write(path: Path, value: str) -> None:
    mode = 0o600
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_nlink != 1:
            raise SyncError(f"unsafe SOUL target: {path}")
        mode = stat.S_IMODE(info.st_mode)
    fd, temporary = tempfile.mkstemp(prefix=".soul-core-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync(instance: Path, seed: Path, *, apply: bool) -> tuple[list[dict], int]:
    core = extract(seed)
    changes = []
    for profile in profile_dirs(instance):
        soul = profile / "SOUL.md"
        try:
            current = soul.read_text(encoding="utf-8") if soul.exists() else ""
        except (OSError, UnicodeError) as exc:
            raise SyncError(f"cannot read SOUL: {soul}") from exc
        wanted = merged(current, core)
        changed = wanted != current
        changes.append({"profile": "default" if profile == instance / "hermes-data" else profile.name,
                        "changed": changed})
        if changed and apply:
            atomic_write(soul, wanted)
    return changes, 0 if apply or not any(item["changed"] for item in changes) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-dir", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        changes, rc = sync(args.instance_dir.resolve(), args.seed.resolve(), apply=args.apply)
        result = {"ok": True, "applied": args.apply, "profiles": changes,
                  "changed": sum(item["changed"] for item in changes)}
    except (SyncError, OSError) as exc:
        result, rc = {"ok": False, "error": str(exc)}, 2
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["ok"]:
        print(f"Fleet SOUL core: {result['changed']} change(s) across {len(result['profiles'])} profile(s)")
    else:
        print(f"Fleet SOUL core error: {result['error']}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
