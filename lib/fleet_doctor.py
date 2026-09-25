#!/usr/bin/env python3
"""Read-only, evidence-limited Hermes fleet doctor for on-disk job and room contracts.

No Hermes import, gateway call, SQLite open, job run, or instance write is allowed.
The execution ledger is deliberately NOT inspected: even SQLite mode=ro can
create -wal/-shm files, while immutable=1 can miss committed WAL records.
Exit codes: 0 = no proven blockers; 1 = proven blocker; 2 = input/read error.
WARN/UNVERIFIED does not make a fleet ready or prove that a job fired, that a
message was delivered, or that any client displayed a room.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable

import yaml

SCHEMA = 1
OVERDUE_GRACE_SECONDS = 15 * 60  # Installed Hermes 0.21.4 hermes_cli/cron.py.
KNOWN_KINDS = frozenset(("once", "interval", "cron"))
SOURCE_LOCATOR_RE = re.compile(r"^[a-z][a-z0-9+.-]{1,31}:[^\s].*$", re.IGNORECASE)
ARTIFACT_DIRECTIVES = (
    re.compile(r"<cycle_dir>|(?:^|[/\\])cycles?[/\\]", re.IGNORECASE),
    re.compile(r"\b(?:cycle[-_ ]?close|retro|handoff|checkpoint|nowcast|proposal|assessment)"
               r"\.(?:md|txt|jsonl?)\b", re.IGNORECASE),
    re.compile(r"\b(?:scheduled|role|cycle|handoff|close|checkpoint|retro)\s+artifact\b",
               re.IGNORECASE),
    re.compile(r"\b(?:write|save|produce|publish|harvest(?:ed)?|own)\b.{0,160}"
               r"\.(?:md|txt|jsonl?)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bfile-first\s+handoff|\bevidence\s+bundle|\brole\s+packet",
               re.IGNORECASE),
)


class InputError(ValueError):
    """An input is unreadable or has an unsupported structure; report rc=2."""


def _json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise InputError(f"cannot read valid JSON: {path}") from exc


def _yaml_file(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeError) as exc:
        raise InputError(f"cannot read valid YAML: {path}") from exc


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None and dt.utcoffset() is not None else None


def _row(checks: list[dict], instance: str, profile: str, check: str,
         status: str, detail: str, *, job_id: str | None = None) -> None:
    entry = {"instance": instance, "profile": profile, "check": check,
             "status": status, "detail": detail}
    if job_id is not None:
        entry["job_id"] = job_id
    checks.append(entry)


def _profile_dirs(home: Path) -> list[tuple[str, Path]]:
    if not (home / "config.yaml").is_file():
        raise InputError(f"profile config missing: {home / 'config.yaml'}")
    result = [("default", home)]
    names_dir = home / "profiles"
    if names_dir.exists():
        if not names_dir.is_dir():
            raise InputError(f"profiles path is not a directory: {names_dir}")
        for child in sorted(names_dir.iterdir()):
            if child.is_dir() and (child / "config.yaml").is_file():
                result.append((child.name, child))
    return result


def _jobs(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = _json_file(path)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise InputError(f"unsupported jobs.json shape (expected object with jobs list): {path}")
    jobs = data["jobs"]
    if any(not isinstance(j, dict) or not isinstance(j.get("id"), str) or not j["id"] for j in jobs):
        raise InputError(f"invalid job row/id in {path}")
    if any(("enabled" in j and not isinstance(j["enabled"], bool))
           or ("state" in j and j["state"] is not None and not isinstance(j["state"], str))
           for j in jobs):
        raise InputError(f"invalid job enabled/state fields in {path}")
    ids = [j["id"] for j in jobs]
    if len(ids) != len(set(ids)):
        raise InputError(f"duplicate job id in {path}")
    return jobs


def _script_issue(script: str, profile_dir: Path) -> str | None:
    # Mirror native path confinement, but never import native code or execute scripts.
    root = (profile_dir / "scripts").resolve()
    try:
        raw = Path(script).expanduser()
        target = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
        target.relative_to(root)
    except (ValueError, RuntimeError, OSError):
        return "script path invalid or resolves outside this profile's scripts directory"
    if not target.is_file():
        return "script not found as a regular file in this profile's scripts directory"
    return None


def _text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _text_values(item)]
    if isinstance(value, dict):
        return [text for key, item in value.items() if key != "durable_output"
                for text in _text_values(item)]
    return []


def _durable_output(value: Any) -> tuple[str, str | None]:
    """Return the declared class and any schema error."""
    if value is None:
        return "none", None
    if not isinstance(value, dict) or not isinstance(value.get("class"), str):
        return "invalid", "durable_output must be an object with a class"
    kind = value["class"]
    if kind in {"none", "native_state", "convergence_ledger"}:
        if set(value) != {"class"}:
            return kind, f"durable_output class {kind} accepts no other fields"
        return kind, None
    if kind == "mission_deliverable":
        if set(value) != {"class", "path", "operator_request"}:
            return kind, "mission_deliverable needs exactly class, path, and operator_request"
        path, request = value.get("path"), value.get("operator_request")
        if not isinstance(path, str) or not path.strip() or "\x00" in path or ".." in Path(path).parts:
            return kind, "mission_deliverable path must be nonempty and contain no parent traversal"
        if not isinstance(request, str) or not SOURCE_LOCATOR_RE.fullmatch(request):
            return kind, "mission_deliverable operator_request must be a stable scheme:locator"
        return kind, None
    return kind, "durable_output class must be none, native_state, convergence_ledger, or mission_deliverable"


def _artifact_culture(job: dict, profile_dir: Path) -> tuple[str, str]:
    declared, issue = _durable_output(job.get("durable_output"))
    if issue:
        return "FAIL", issue
    text = "\n".join(_text_values(job))
    script = job.get("script")
    if isinstance(script, str) and _script_issue(script, profile_dir) is None:
        root = (profile_dir / "scripts").resolve()
        raw = Path(script).expanduser()
        target = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
        try:
            if target.stat().st_size <= 1_000_000:
                text += "\n" + target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            pass
    signals = sorted({pattern.pattern for pattern in ARTIFACT_DIRECTIVES if pattern.search(text)})
    if signals and declared != "mission_deliverable":
        return ("FAIL", "active job directs an internal file/artifact workflow; use native Kanban/room "
                "state and the convergence ledger instead (or explicitly bind a real mission deliverable "
                "to its operator-request locator)")
    if signals:
        return ("PASS", "file output is explicitly declared as an operator-requested mission deliverable; "
                "runtime behavior and request validity are not verified")
    return ("PASS", f"no internal artifact-workflow directive found; durable output class is {declared}; "
            "runtime behavior is not executed")


def _job_checks(checks: list[dict], instance: str, profile: str, root: Path,
                job: dict, now: datetime) -> None:
    job_id = job["id"]
    # Native is_job_runnable defaults a missing enabled to True, but a
    # contradictory paused_at marker also prevents dispatch.
    if job.get("enabled", True) is False or job.get("state") in {"paused", "completed"} or job.get("paused_at"):
        return
    schedule = job.get("schedule")
    kind = schedule.get("kind") if isinstance(schedule, dict) else None
    if kind not in KNOWN_KINDS:
        _row(checks, instance, profile, "schedule", "FAIL",
             "active job has no supported schedule.kind (once, interval, cron)", job_id=job_id)
    else:
        fields_ok = ((kind == "once" and _time(schedule.get("run_at")) is not None)
                     or (kind == "interval" and isinstance(schedule.get("minutes"), (int, float))
                         and not isinstance(schedule["minutes"], bool)
                         and 0 < schedule["minutes"] < float("inf"))
                     or (kind == "cron" and isinstance(schedule.get("expr"), str)
                         and len(schedule["expr"].split()) in (5, 6)))
        _row(checks, instance, profile, "schedule", "PASS" if fields_ok else "FAIL",
             "kind and required field have an expected basic shape; cron syntax and native compute not verified"
             if fields_ok else "schedule kind has a missing/invalid required field", job_id=job_id)
    next_at = _time(job.get("next_run_at"))
    _row(checks, instance, profile, "next_run_at", "PASS" if next_at else "FAIL",
         "active job has a timezone-aware next_run_at; firing is not verified" if next_at
         else "active job has no valid timezone-aware next_run_at and cannot be shown armed",
         job_id=job_id)
    if next_at and (now - next_at).total_seconds() > OVERDUE_GRACE_SECONDS:
        _row(checks, instance, profile, "overdue", "WARN",
             "next_run_at is over 15 minutes past; this can mean a stalled ticker, a slow running job, or a racing read",
             job_id=job_id)
    if script := job.get("script"):
        if not isinstance(script, str):
            _row(checks, instance, profile, "script", "FAIL", "script path is not a string", job_id=job_id)
        else:
            issue = _script_issue(script, root)
            _row(checks, instance, profile, "script", "FAIL" if issue else "PASS",
                 issue or "script file exists and is confined; not executed", job_id=job_id)
    elif job.get("no_agent"):
        _row(checks, instance, profile, "script", "FAIL", "active no_agent job has no script", job_id=job_id)
    target = job.get("deliver", "local")
    failed_target = job.get("failure_deliver") or target
    if target in (None, "local", "") or failed_target in (None, "local", ""):
        _row(checks, instance, profile, "delivery_route", "WARN",
             "local/empty success or failure delivery has no remote recipient; may be intentional for script jobs; declare a real consumer where notification is required",
             job_id=job_id)
    else:
        _row(checks, instance, profile, "delivery_route", "UNVERIFIED",
             "delivery target is configured, but recipient, acceptance, and visibility are unverified",
             job_id=job_id)
    if job.get("last_delivery_error") or job.get("last_delivery_unverified"):
        _row(checks, instance, profile, "last_delivery", "WARN",
             "last delivery failed or adapter ACK did not prove delivery; inspect native job state",
             job_id=job_id)
    artifact_status, artifact_detail = _artifact_culture(job, root)
    _row(checks, instance, profile, "artifact_culture", artifact_status,
         artifact_detail, job_id=job_id)


# Coordinated upstream policy: Desktop group-chat.ts must ship together with
# gateway methods_profiles.py. Neither installed client nor gateway version is
# available from this disk-only doctor; old clients may still enforce 48,000
# and old gateways may still reject incoming ui_meta beyond 65,536 characters.
DESKTOP_SYNC_LIMIT = 192_000  # group-chat.ts GROUP_CHAT_SYNC_MAX_BYTES (patched).
LEGACY_DESKTOP_SYNC_LIMIT = 48_000
GATEWAY_UI_META_LIMIT = 262_144  # methods_profiles.py len(json.dumps(incoming)).
LEGACY_GATEWAY_UI_META_LIMIT = 65_536
# Advisory room + post reserve, not a guarantee against future client trimming.
DESKTOP_SYNC_MIN_HEADROOM = 4_000


def _desktop_gateway_size(value: Any) -> int:
    """groupChatGatewayJsonSize: compact JS JSON plus Python escape reserve."""
    compact = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    total = 0
    for char in compact:
        code = ord(char)
        if code == 0x7f:  # JS prints DEL literally; Python escapes it as \u007f.
            total += 6
        elif code <= 0x7f:
            total += 1 + (char in ",:")
        else:
            total += 6 if code <= 0xffff else 12
    return total


def _gateway_ui_meta_size(registry: dict) -> int:
    """Gateway's character count for a single-key Desktop ui_meta request."""
    return len(json.dumps({"hermes-bots-groups": registry}))


def _member_ids(room: Any) -> list[str]:
    """Remote member connection ids saved in one ui_meta room, in order."""
    members = room.get("members") if isinstance(room, dict) else None
    ids: list[str] = []
    for member in members if isinstance(members, list) else []:
        if not isinstance(member, dict):
            continue
        value = member.get("connectionId")
        if isinstance(value, str) and value.strip() and value.strip() != "local":
            ids.append(value.strip())
    return ids


def _room_check(checks: list[dict], instance: str, profile: str, home: Path,
                verified_connection_ids: frozenset[str] = frozenset()) -> None:
    meta_path = home / "profile.yaml"
    if not meta_path.exists():
        _row(checks, instance, profile, "desktop_registry", "UNVERIFIED",
             "profile.yaml absent; Desktop registry not measured")
        return
    meta = _yaml_file(meta_path)
    if not isinstance(meta, dict):
        raise InputError(f"profile.yaml must be a mapping: {meta_path}")
    ui = meta.get("ui_meta")
    registry = ui.get("hermes-bots-groups") if isinstance(ui, dict) else None
    if registry is not None and not isinstance(registry, dict):
        raise InputError(f"Desktop group registry must be a mapping: {meta_path}")
    rooms = registry.get("rooms") if isinstance(registry, dict) else None
    if rooms is not None and not isinstance(rooms, dict):
        raise InputError(f"Desktop rooms registry must be a mapping: {meta_path}")
    count = len(rooms) if isinstance(rooms, dict) else 0
    _row(checks, instance, profile, "desktop_registry", "UNVERIFIED",
         f"{count} Desktop ui_meta room(s) on disk; client sync, visibility, backend rooms, and delivery not verified")
    if isinstance(registry, dict) and isinstance(rooms, dict):
        # groupChatSyncEnvelope() may omit whole rooms without tombstones. The
        # saved per-gateway projection is not the client's full local state.
        used = _desktop_gateway_size(registry)
        incoming = _gateway_ui_meta_size(registry)
        headroom = DESKTOP_SYNC_LIMIT - used
        legacy_headroom = LEGACY_DESKTOP_SYNC_LIMIT - used
        detail = (f"Saved Desktop projection estimates {used}/{DESKTOP_SYNC_LIMIT} "
                  f"conservative bytes (headroom {headroom}); single-key gateway "
                  f"ui_meta incoming estimate {incoming}/{GATEWAY_UI_META_LIMIT} "
                  "Python JSON characters. Installed client/gateway versions and "
                  "other incoming ui_meta keys are unverified. ")
        risks = []
        if headroom < DESKTOP_SYNC_MIN_HEADROOM:
            risks.append("new Desktop may omit another room without a tombstone")
        if incoming > GATEWAY_UI_META_LIMIT:
            risks.append("even a new gateway may reject this incoming ui_meta")
        if legacy_headroom < DESKTOP_SYNC_MIN_HEADROOM:
            risks.append(f"old Desktop (48,000-byte cap; headroom {legacy_headroom}) "
                         "may omit a room without a tombstone")
        if incoming > LEGACY_GATEWAY_UI_META_LIMIT:
            risks.append("old gateway (65,536-character cap) may reject this incoming ui_meta")
        if risks:
            _row(checks, instance, profile, "desktop_room_capacity", "WARN",
                 detail + "; ".join(risks) + ". Back up client state; "
                 "upgrade gateway first, then verify the Desktop version and projection. "
                 "Do not trim unrelated room logs or infer client deletion")
        else:
            _row(checks, instance, profile, "desktop_room_capacity", "PASS",
                 detail + "Measured projection has at least 4,000 bytes of "
                 "legacy Desktop headroom; future growth, client visibility and "
                 "successful writes are still unverified")
    # Connection identity of saved room seats. A Desktop registry id is minted
    # from the connection LABEL the operator typed; it is NEVER derivable from
    # a gateway hostname, URL, or tailnet name, and a rename keeps the old id.
    # Desktop's roster gateway filter keeps a room only when some seated member
    # connectionId equals the selected source id, so a wrong id renders the room
    # under "all" and hides it under its own gateway. The host cannot read the
    # operator's connections.json, so ids are UNVERIFIED unless supplied.
    for key, room in sorted((rooms or {}).items()):
        if not isinstance(room, dict) or room.get("tombstone"):
            continue
        ids = _member_ids(room)
        if not ids:
            continue
        unique = sorted(set(ids))
        if not verified_connection_ids:
            _row(checks, instance, profile, "room_connection_identity", "UNVERIFIED",
                 f"{key}: {len(ids)} remote seat(s) on {', '.join(unique)}; no operator-verified "
                 "connection id supplied (--verified-connection-id). Copy the id from the Desktop "
                 "registry (connections.json id / host.agents source); never derive it from a hostname")
            continue
        unknown = sorted(set(ids) - verified_connection_ids)
        if unknown:
            _row(checks, instance, profile, "room_connection_identity", "WARN",
                 f"{key}: seat connection id(s) {', '.join(unknown)} are not operator-verified "
                 f"(verified: {', '.join(sorted(verified_connection_ids))}); Desktop's gateway filter "
                 "hides the room under that gateway and turns cannot route to those seats")
        if not set(ids) & verified_connection_ids:
            _row(checks, instance, profile, "room_connection_identity", "WARN",
                 f"{key}: no seat uses a verified connection id; the room cannot appear under any "
                 "verified gateway filter")
        elif not unknown:
            _row(checks, instance, profile, "room_connection_identity", "PASS",
                 f"{key}: all {len(ids)} remote seat(s) use operator-verified connection id(s) "
                 f"{', '.join(unique)}; client rendering and delivery still unverified")


def check_instance(instance_dir: Path, now: datetime | None = None,
                   verified_connection_ids: frozenset[str] = frozenset()) -> list[dict]:
    instance_dir = Path(instance_dir)
    if not instance_dir.is_dir():
        raise InputError(f"instance directory missing: {instance_dir}")
    home = instance_dir / "hermes-data"
    profiles = _profile_dirs(home)
    instance = instance_dir.name
    now = now or datetime.now(timezone.utc)
    checks: list[dict] = []
    for profile, path in profiles:
        manifest = path / "cron" / "jobs.json"
        jobs = _jobs(manifest)
        active = [j for j in jobs if j.get("enabled", True) is not False
                  and j.get("state") not in {"paused", "completed"} and not j.get("paused_at")]
        _row(checks, instance, profile, "job_manifest", "PASS" if manifest.is_file() else "UNVERIFIED",
             f"{len(jobs)} job(s), {len(active)} active; an on-disk snapshot does not prove scheduler ownership"
             if manifest.is_file() else "no jobs.json; no schedule claim")
        for job in jobs:
            _job_checks(checks, instance, profile, path, job, now)
        handoff = path / "handoff"
        if handoff.exists():
            _row(checks, instance, profile, "artifact_culture", "FAIL",
                 "legacy handoff path exists; Kanban transitions and room turns are the handoff surface")
        _room_check(checks, instance, profile, path, verified_connection_ids)
    _row(checks, instance, "*", "unknown_outcomes", "UNVERIFIED",
         "live execution DB deliberately not opened; unknown outcomes cannot be ruled out")
    _row(checks, instance, "*", "token_budget", "UNVERIFIED",
         "no live usage DB opened; per-cycle token use, cache ratio, and spending caps cannot be measured")
    _row(checks, instance, "*", "client_visibility", "UNVERIFIED",
         "backend room ACK and on-disk Desktop registry do not establish human-visible delivery")
    return checks


def _required_room_seats(checks: list[dict], instance_dir: Path,
                         required: dict[str, str]) -> None:
    """Fail closed on an operator-named Desktop room; never infer the client id."""
    if not required:
        return
    profile_path = instance_dir / "hermes-data" / "profile.yaml"
    if not profile_path.is_file():
        rooms = {}
    else:
        meta = _yaml_file(profile_path)
        if not isinstance(meta, dict):
            raise InputError(f"profile.yaml must be a mapping: {profile_path}")
        ui = meta.get("ui_meta")
        registry = ui.get("hermes-bots-groups") if isinstance(ui, dict) else None
        rooms = registry.get("rooms") if isinstance(registry, dict) else None
        if rooms is not None and not isinstance(rooms, dict):
            raise InputError(f"Desktop rooms registry must be a mapping: {profile_path}")
    rooms = rooms or {}
    for key, expected in required.items():
        room = rooms.get(key)
        members = room.get("members") if isinstance(room, dict) else None
        if not isinstance(members, list) or not 2 <= len(members) <= 6 or room.get("tombstone"):
            _row(checks, instance_dir.name, "default", "required_room_seats", "FAIL",
                 f"{key}: missing, tombstoned, or not 2-6 saved members; do not activate this room")
            continue
        if any(not isinstance(m, dict) or m.get("connectionId") != expected or
               m.get("connectionKind") != "remote" or not isinstance(m.get("name"), str) or
               not m["name"] for m in members) or len({m["name"] for m in members}) != len(members):
            _row(checks, instance_dir.name, "default", "required_room_seats", "FAIL",
                 f"{key}: saved members do not all match the supplied Desktop connection id "
                 f"{expected} (remote); ghost seats, duplicates, and a hidden gateway filter are possible")
            continue
        _row(checks, instance_dir.name, "default", "required_room_seats", "PASS",
             f"{key}: {len(members)} remote seats match the supplied Desktop connection id "
             f"{expected}; client filter, reachability, and replies still need a human check")


def run(*, instance_dir: Path | None = None, instances_dir: Path | None = None,
        now: datetime | None = None, verified_connection_ids: Iterable[str] = (),
        require_room_connection: Iterable[str] = ()) -> tuple[dict, int]:
    report = {"schema": SCHEMA, "read_only": True, "scope": "disk-only-no-db-no-runtime",
              "instances": [], "checks": [], "summary": {"FAIL": 0, "WARN": 0, "UNVERIFIED": 0, "PASS": 0}}
    try:
        if (instance_dir is None) == (instances_dir is None):
            raise InputError("specify exactly one of --instance-dir and --instances-dir")
        if instance_dir is not None:
            paths = [Path(instance_dir)]
        else:
            base = Path(instances_dir)
            if not base.is_dir():
                raise InputError(f"instances directory missing: {base}")
            paths = sorted(p for p in base.iterdir() if p.is_dir() and (p / "control.env").is_file())
            if not paths:
                raise InputError(f"no instance directories with control.env: {base}")
        verified = frozenset(str(value).strip() for value in verified_connection_ids
                             if str(value).strip())
        report["verified_connection_ids"] = sorted(verified)
        required: dict[str, str] = {}
        if require_room_connection and instance_dir is None:
            raise InputError("--require-room-connection needs --instance-dir (one gateway at a time)")
        for value in require_room_connection:
            key, sep, connection_id = value.partition("=")
            if (not sep or not key.startswith(("id:", "name:")) or
                    not key.split(":", 1)[1] or not connection_id or connection_id == "local"):
                raise InputError("--require-room-connection needs "
                                 "id:<room>=<Desktop id> or name:<room>=<Desktop id>")
            if key in required and required[key] != connection_id:
                raise InputError(f"conflicting Desktop connection ids for room: {key}")
            required[key] = connection_id
        if set(required.values()) - verified:
            raise InputError("--require-room-connection ids must also be supplied with "
                             "--verified-connection-id from the affected Desktop")
        report["required_room_connections"] = required
        for path in paths:
            report["instances"].append(path.name)
            report["checks"].extend(check_instance(path, now=now,
                                                   verified_connection_ids=verified))
            _required_room_seats(report["checks"], path, required)
        for item in report["checks"]:
            report["summary"][item["status"]] += 1
    except (InputError, OSError) as exc:
        report["error"] = str(exc)
        return report, 2
    return report, 1 if report["summary"]["FAIL"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--instance-dir", type=Path)
    group.add_argument("--instances-dir", type=Path)
    parser.add_argument("--verified-connection-id", action="append", default=[],
                        metavar="ID",
                        help="operator-verified Desktop connection id (repeatable). Read it from the "
                             "Desktop connection registry; it is never derived from a gateway hostname")
    parser.add_argument("--require-room-connection", action="append", default=[],
                        metavar="ROOM_KEY=DESKTOP_ID",
                        help="fail if the exact Desktop room is absent or any of its 2-6 remote "
                             "seats uses another id; requires --instance-dir and an operator-verified id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, rc = run(instance_dir=args.instance_dir, instances_dir=args.instances_dir,
                     verified_connection_ids=args.verified_connection_id,
                     require_room_connection=args.require_room_connection)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Fleet doctor: {len(result['instances'])} instance(s), "
              f"{result['summary']['FAIL']} FAIL, {result['summary']['WARN']} WARN, "
              f"{result['summary']['UNVERIFIED']} UNVERIFIED")
        for row in result["checks"]:
            if row["status"] in {"FAIL", "WARN"}:
                print(f"  {row['status']} {row['instance']}/{row['profile']} "
                      f"{row['check']} {row.get('job_id', '')}: {row['detail']}")
        if "error" in result:
            print(f"ERROR {result['error']}", file=sys.stderr)
        print("Disk check only: no jobs fired, no execution DB opened, no delivery/visibility verified.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
