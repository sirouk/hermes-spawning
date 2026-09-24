#!/usr/bin/env python3
"""Formation intent declaration and read-only status; neither activates a crew.

CLI: python3 lib/formation_status.py --instance-dir instances/NAME
     python3 lib/formation_status.py --instances-dir instances
     python3 lib/formation_status.py declare --instance-dir instances/NAME --mode solo|crew

Only an operator-controlled receipt at instances/NAME/formation-acceptance.json
can declare {"schema": 1, "instance": "NAME", "mode": "solo"|"crew"}.
This file must be a regular 0600 file in the private, host-side instance dir,
not in the agent-writable hermes-data mount. An absent receipt is UNKNOWN, never
implicitly solo. Two or more named native profiles refute a solo declaration;
the default profile is not counted. A crew is always PENDING, even if its
receipt claims successful formation: no independent live-cycle validator is
implemented here. This command never grants permission to perform domain work,
pauses schedules, writes evidence, probes models, or declares QUALIFIED.

Exit 0: explicit solo or unknown with at most one named profile (not qualified).
Exit 1: crew pending, unknown crew, invalid/conflicting declaration.
Exit 2: operational/input/read error. JSON is the only status output.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import sys

RECEIPT = "formation-acceptance.json"


class OperationalError(Exception):
    """The host-side directory or receipt could not safely be read."""


def _profiles(instance: Path) -> list[str]:
    home = instance / "hermes-data"
    if home.is_symlink() or not home.is_dir():
        raise OperationalError("instance hermes-data is missing or symlinked")
    profiles = home / "profiles"
    if profiles.is_symlink():
        raise OperationalError("profile directory is symlinked")
    if not profiles.exists():
        return []  # Native root/default exists independently of named profiles.
    if not profiles.is_dir():
        raise OperationalError("profile directory is not a directory")
    try:
        result = []
        for child in profiles.iterdir():
            if child.name.startswith("."):
                continue  # Native .deleted and other hidden bookkeeping are not crew.
            if child.is_symlink():
                raise OperationalError("named profile is symlinked")
            if child.is_dir():
                result.append(child.name)
        return sorted(result)
    except OSError as exc:
        raise OperationalError("cannot list native profiles") from exc


def _receipt(instance: Path) -> tuple[dict | None, str | None]:
    path = instance / RECEIPT
    if not path.exists() and not path.is_symlink():
        return None, None
    try:
        if path.is_symlink():
            return None, "operator receipt must not be a symlink"
        # Open without following a symlink substituted between the type check
        # and readback. A receipt is host-side metadata, never agent evidence.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            metadata = os.fstat(handle.fileno())
            parent = instance.stat()
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != parent.st_uid
                    or (metadata.st_mode & 0o777) != 0o600
                    or (parent.st_mode & 0o077) != 0):
                return None, "operator receipt has unsafe type, owner, or permissions"
            content = json.load(handle)
    except (OSError, UnicodeError) as exc:
        raise OperationalError("cannot read operator receipt") from exc
    except (ValueError, TypeError):
        return None, "operator receipt is invalid JSON"
    if (not isinstance(content, dict) or type(content.get("schema")) is not int
            or content.get("schema") != 1 or content.get("instance") != instance.name
            or content.get("mode") not in ("solo", "crew")):
        return None, "operator receipt schema, instance, or mode is invalid"
    return content, None


def declare(instance: Path, mode: str) -> str:
    """Record operator intent, not acceptance or qualification.

    Only the operator-owned private instance directory, outside the agent's
    hermes-data bind mount, may hold this receipt. The directory lock
    serializes declarations made through this helper;
    publish uses an exclusive link for new files and an atomic replace for the
    one permitted transition, solo -> crew. An unrecognized or enriched receipt
    (including one bearing acceptance/evidence fields) is never overwritten.
    """
    if mode not in ("solo", "crew"):
        raise ValueError("formation mode must be solo or crew")
    instance = Path(instance)
    # Open each path component relative to a directory fd. O_NOFOLLOW on
    # only the final path would permit a symlink in an intermediate directory.
    if not instance.name or ".." in instance.parts:
        raise OperationalError("unsafe instance path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        directory = os.open("/" if instance.is_absolute() else ".", flags)
        try:
            for component in instance.parts:
                if component in ("/", "."):
                    continue
                next_dir = os.open(component, flags, dir_fd=directory)
                os.close(directory)
                directory = next_dir
        except BaseException:
            os.close(directory)
            raise
    except OSError as exc:
        raise OperationalError("instance directory missing or unsafe") from exc
    try:
        parent = os.fstat(directory)
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
                or parent.st_mode & 0o077):
            raise OperationalError("instance directory must be private and operator-owned")
        fcntl.flock(directory, fcntl.LOCK_EX)
        data = {"schema": 1, "instance": instance.name, "mode": mode}
        existing = None
        try:
            receipt = os.open(RECEIPT, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise OperationalError("operator receipt is unsafe or cannot be read") from exc
        else:
            with os.fdopen(receipt, "r", encoding="utf-8") as handle:
                metadata = os.fstat(handle.fileno())
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != parent.st_uid
                        or metadata.st_mode & 0o777 != 0o600
                        or metadata.st_nlink != 1):
                    raise OperationalError("operator receipt has unsafe type, owner, or permissions")
                try:
                    def no_duplicate_fields(fields: list[tuple[str, object]]) -> dict:
                        if len(dict(fields)) != len(fields):
                            raise ValueError("duplicate receipt field")
                        return dict(fields)

                    existing = json.load(handle, object_pairs_hook=no_duplicate_fields)
                except (ValueError, TypeError, UnicodeError) as exc:
                    raise OperationalError("operator receipt is invalid JSON") from exc
            if (not isinstance(existing, dict) or set(existing) != set(data)
                    or type(existing.get("schema")) is not int or existing["schema"] != 1
                    or existing["instance"] != instance.name
                    or existing["mode"] not in ("solo", "crew")):
                raise OperationalError("operator receipt is not a plain intent declaration; refusing overwrite")
            if existing["mode"] == mode:
                return "unchanged"
            if existing["mode"] != "solo" or mode != "crew":
                raise OperationalError("crew declaration cannot be changed to solo")
            # Refuse to replace a receipt swapped after it was inspected.
            current = os.stat(RECEIPT, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
                    metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns):
                raise OperationalError("operator receipt changed during declaration")
        temporary = ".formation-intent-" + secrets.token_hex(16)
        temp_fd = None
        try:
            temp_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                              | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
            with os.fdopen(temp_fd, "w", encoding="utf-8") as output:
                temp_fd = None
                json.dump(data, output, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            if existing is None:
                # Atomic create without replacing a competing receipt.
                os.link(temporary, RECEIPT, src_dir_fd=directory, dst_dir_fd=directory,
                        follow_symlinks=False)
            else:
                os.replace(temporary, RECEIPT, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            if temp_fd is not None:
                os.close(temp_fd)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        return "upgraded" if existing is not None else "created"
    except OSError as exc:
        raise OperationalError("cannot safely write operator formation declaration") from exc
    finally:
        os.close(directory)


def declare_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Record host-side formation intent (not qualification)")
    parser.add_argument("--instance-dir", type=Path, required=True)
    parser.add_argument("--mode", required=True, choices=("solo", "crew"))
    args = parser.parse_args(argv)
    try:
        outcome = declare(args.instance_dir, args.mode)
    except (OperationalError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if outcome == "upgraded":
        print("Formation intent changed from solo to crew (not qualified).")
    else:
        print(f"Formation intent {outcome}: {args.mode} (not qualified).")
    return 0


def inspect(instance: Path) -> dict:
    instance = Path(instance)
    name = instance.name
    result = {"instance": name, "status": "unknown", "formation_qualified": False,
              "named_profiles": [], "findings": []}
    try:
        if instance.is_symlink() or not instance.is_dir():
            raise OperationalError("instance directory missing or symlinked")
        if not (instance / "control.env").is_file():
            raise OperationalError("instance control.env is missing")
        result["named_profiles"] = _profiles(instance)
        receipt, error = _receipt(instance)
        if error:
            result["status"] = "blocked"
            result["findings"].append(error)
        elif receipt is None:
            result["status"] = "unknown-crew" if len(result["named_profiles"]) >= 2 else "unknown"
            result["findings"].append("no operator formation declaration (no qualification claim)")
        elif receipt["mode"] == "solo":
            if len(result["named_profiles"]) >= 2:
                result["status"] = "blocked"
                result["findings"].append("solo declaration conflicts with multiple named profiles")
            else:
                result["status"] = "solo"
        else:
            result["status"] = "pending"
            result["findings"].append("crew lacks independent live formation-cycle verification")
        # Never consume assertions such as `qualified`, `checks`, or `evidence`
        # from either an agent-written artifact or this host receipt as proof.
    except (OSError, OperationalError) as exc:
        result["status"] = "error"
        result["findings"] = [str(exc) if isinstance(exc, OperationalError)
                              else "cannot inspect instance"]
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--instance-dir", type=Path)
    group.add_argument("--instances-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.instance_dir is not None:
            entries = [args.instance_dir]
        else:
            root = args.instances_dir
            if root.is_symlink() or not root.is_dir():
                raise OperationalError("instances directory missing or symlinked")
            entries = sorted((child for child in root.iterdir()
                              if child.is_dir() and ((child / "control.env").exists()
                                                     or (child / "hermes-data").exists())),
                             key=lambda child: child.name)
            if not entries:
                raise OperationalError("no instances found")
        rows = [inspect(path) for path in entries]
        code = (2 if any(row["status"] == "error" for row in rows)
                else 1 if any(row["status"] in ("pending", "blocked", "unknown-crew") for row in rows)
                else 0)
        output = {"read_only": True, "formation_qualified": False, "instances": rows}
    except (OSError, OperationalError) as exc:
        code = 2
        output = {"read_only": True, "formation_qualified": False, "instances": [],
                  "error": str(exc) if isinstance(exc, OperationalError) else "cannot list instances"}
    print(json.dumps(output, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(declare_main(sys.argv[2:]) if sys.argv[1:2] == ["declare"]
                     else main())
