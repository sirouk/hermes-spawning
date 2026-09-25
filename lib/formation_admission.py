#!/usr/bin/env python3
"""Read-only admission check for a six-persona, native-state Hermes fleet.

This v2 gate binds one private operator ledger row to the current runtime
contract and native event locators. It deliberately requires no role reports,
evidence bundle, screenshots, handoff files, cycle files, or hash tree.

The operator creates INSTANCE/formation-admission.json (0600):

{
  "schema": 2,
  "instance": "fleet-name",
  "scope": "mission-buildout",
  "accepted_at": "2026-09-24T18:00:00Z",
  "map_version": "v1",
  "contract": "hermes-data/fleet-runtime.yaml",
  "member_connection": {"id": "desktop-source-id", "kind": "remote",
                        "label": "operator-visible label"},
  "operator_declaration": {"name": "operator", "signed_at": "...Z",
    "statement": "I accept this crew and map for mission-buildout only"},
  "client_witness": {"observer": "operator", "observed_at": "...Z",
    "physically_seen": true,
    "room_keys": ["id:coordination", "id:improvement"],
    "post_id": "native-post-id", "reply_id": "native-reply-id",
    "reply_profile": "one-of-the-six"},
  "independent_assessor": {"name": "different person", "reviewed_at": "...Z",
    "finding": "pass"},
  "native_events": {
    "role_drills": {"ROLE": "kanban:board/card/event", "...": "..."},
    "schedule_runs": {"ROLE": "cron:profile/job/execution", "...": "..."},
    "handoffs": ["kanban:board/card/event"],
    "room_post": "room:key/event", "room_reply": "room:key/event",
    "stop_test": "runtime:stop/test-id",
    "recovery_test": "runtime:recovery/test-id",
    "independent_assessment": "kanban:board/card/event",
    "convergence_recall": "ledger:query/event-id",
    "retry_guard": "ledger:guard/event-id"
  }
}

The runtime contract is executable configuration, not a status essay. Its
validated shape is documented by `_runtime_contract` below. Current profile,
shared-skill, board, room, schedule, and anti-artifact invariants are checked
directly. Native event locators and human identity remain attestations: this
tool does not contact a live Desktop client or authenticate a person, and it is
not itself an execution sandbox. Keep consequential credentials behind the
real operator-controlled admission boundary.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

import yaml

import fleet_doctor


SCOPE = "mission-buildout"
RECEIPT = "formation-admission.json"
REQUIRED_FUNCTIONS = frozenset({"sensing", "proposal", "challenge", "execution",
                                "decision", "stewardship"})
REQUIRED_STATES = ("READY", "OBSERVING", "ORIENTING", "DECIDING", "AUTHORIZING",
                   "ACTING", "VERIFYING", "LEARNING", "WAITING", "BLOCKED",
                   "HOLDING", "STOPPED")
NORMAL_EDGES = (("READY", "OBSERVING"), ("OBSERVING", "ORIENTING"),
                ("ORIENTING", "DECIDING"), ("DECIDING", "AUTHORIZING"),
                ("AUTHORIZING", "ACTING"), ("ACTING", "VERIFYING"),
                ("VERIFYING", "LEARNING"), ("LEARNING", "READY"))
MEETING_TRIGGERS = frozenset({"material_disagreement", "ambiguity", "consequence",
                              "cross_role_dependency", "invalidated_assumption"})
LOCATORS = {
    "kanban": re.compile(r"^kanban:[^\s]+$"),
    "cron": re.compile(r"^cron:[^\s]+$"),
    "room": re.compile(r"^room:[^\s]+$"),
    "runtime": re.compile(r"^runtime:[^\s]+$"),
    "ledger": re.compile(r"^ledger:[^\s]+$"),
    "source": re.compile(r"^[a-z][a-z0-9+.-]{1,31}:[^\s]+$", re.IGNORECASE),
}


class Denied(ValueError):
    pass


def need(condition: bool, code: str) -> None:
    if not condition:
        raise Denied(code)


def exact(value: Any, keys: set[str], code: str) -> dict:
    need(isinstance(value, dict) and set(value) == keys, code)
    return value


def text(value: Any, code: str, *, max_length: int = 512) -> str:
    need(isinstance(value, str) and value.strip() == value and 0 < len(value) <= max_length
         and "\x00" not in value and "\n" not in value and "\r" not in value, code)
    return value


def timestamp(value: Any, code: str) -> datetime:
    raw = text(value, code, max_length=64)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Denied(code) from exc
    need(parsed.tzinfo is not None and parsed.utcoffset() is not None, code)
    return parsed.astimezone(timezone.utc)


def regular(path: Path, code: str, *, private: bool = False) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise Denied(code) from exc
    need(stat.S_ISREG(info.st_mode) and not path.is_symlink() and info.st_nlink == 1, code)
    need(info.st_uid == os.geteuid(), code)
    if private:
        need(stat.S_IMODE(info.st_mode) == 0o600, code)
    return path


def safe_relative(root: Path, value: Any, code: str) -> Path:
    raw = text(value, code)
    parts = PurePosixPath(raw).parts
    need(not PurePosixPath(raw).is_absolute() and parts and all(part not in {"", ".", ".."}
                                                               for part in parts), code)
    target = root.joinpath(*parts)
    try:
        target.resolve().relative_to(root.resolve())
    except (OSError, ValueError, RuntimeError) as exc:
        raise Denied(code) from exc
    return target


def locator(value: Any, kind: str, code: str) -> str:
    raw = text(value, code)
    need(bool(LOCATORS[kind].fullmatch(raw)), code)
    return raw


def string_list(value: Any, code: str, *, minimum: int = 1) -> list[str]:
    need(isinstance(value, list) and len(value) >= minimum, code)
    result = [text(item, code) for item in value]
    need(len(result) == len(set(result)), code)
    return result


def _profiles(instance: Path, rows: list[dict]) -> tuple[dict[str, str], dict[str, Path]]:
    need(isinstance(rows, list) and len(rows) == 6, "invalid_personas")
    roles: dict[str, str] = {}
    paths: dict[str, Path] = {}
    functions: set[str] = set()
    decision_profiles: set[str] = set()
    for row in rows:
        row = exact(row, {"role", "profile", "responsibility", "functions", "tools",
                          "skills", "authority"}, "invalid_personas")
        role = text(row["role"], "invalid_personas", max_length=80)
        profile = text(row["profile"], "invalid_personas", max_length=80)
        need(re.fullmatch(r"[A-Za-z0-9_-]+", profile) is not None, "invalid_personas")
        text(row["responsibility"], "invalid_personas")
        owned = set(string_list(row["functions"], "invalid_personas"))
        tools = string_list(row["tools"], "invalid_personas")
        skills = string_list(row["skills"], "invalid_personas")
        string_list(row["authority"], "invalid_personas")
        need(all(re.fullmatch(r"[a-z][a-z0-9_-]*", name) for name in owned),
             "invalid_personas")
        need("fleet-convergence-learning" in skills and tools, "missing_convergence_capability")
        need(role not in roles and profile not in paths, "invalid_personas")
        roles[role] = profile
        base = instance / "hermes-data" if profile == "default" else instance / "hermes-data/profiles" / profile
        need(base.is_dir() and not base.is_symlink(), "missing_profile")
        for filename in ("SOUL.md", "config.yaml", "profile.yaml"):
            regular(base / filename, "missing_profile")
        need(not (base / "handoff").exists(), "legacy_file_handoff")
        functions.update(owned)
        if "decision" in owned:
            decision_profiles.add(profile)
        paths[profile] = base
    need("default" in paths and len(paths) == 6, "invalid_personas")
    need(REQUIRED_FUNCTIONS <= functions, "missing_function_coverage")
    need(len(decision_profiles) == 1, "invalid_decision_ownership")
    return roles, paths


def _surfaces(instance: Path, value: Any) -> tuple[dict, list[str]]:
    value = exact(value, {"truth", "flow", "deliberation", "memory", "cognition", "timing"},
                  "invalid_surfaces")
    need(value["truth"] == "authoritative-tools", "invalid_surfaces")
    need(value["cognition"] == "ephemeral-session", "invalid_surfaces")
    need(value["timing"] == "native-schedules-and-triggers", "invalid_surfaces")
    flow = exact(value["flow"], {"kind", "coordination_board", "improvement_board"},
                 "invalid_surfaces")
    need(flow["kind"] == "kanban", "invalid_surfaces")
    boards = [text(flow[key], "invalid_surfaces", max_length=100)
              for key in ("coordination_board", "improvement_board")]
    need(len(set(boards)) == 2 and all(re.fullmatch(r"[A-Za-z0-9_-]+", board) for board in boards),
         "invalid_surfaces")
    for board in boards:
        directory = instance / "hermes-data/kanban/boards" / board
        need(directory.is_dir() and not directory.is_symlink()
             and ((directory / "board.json").is_file() or (directory / "kanban.db").is_file()),
             "missing_native_board")
    rooms = exact(value["deliberation"], {"kind", "coordination_room", "improvement_room"},
                  "invalid_surfaces")
    need(rooms["kind"] == "group-chat", "invalid_surfaces")
    room_keys = [text(rooms[key], "invalid_surfaces")
                 for key in ("coordination_room", "improvement_room")]
    need(len(set(room_keys)) == 2 and all(key.startswith(("id:", "name:")) for key in room_keys),
         "invalid_surfaces")
    memory = exact(value["memory"], {"kind", "path", "recall_limit", "char_budget"},
                   "invalid_surfaces")
    need(memory["kind"] == "convergence-ledger"
         and isinstance(memory["recall_limit"], int) and not isinstance(memory["recall_limit"], bool)
         and 1 <= memory["recall_limit"] <= 10
         and isinstance(memory["char_budget"], int) and not isinstance(memory["char_budget"], bool)
         and 256 <= memory["char_budget"] <= 8000, "invalid_surfaces")
    path = safe_relative(instance, memory["path"], "invalid_surfaces")
    need(path == instance / "hermes-data/fleet-state/convergence.db", "invalid_surfaces")
    return {"boards": boards, "rooms": room_keys}, room_keys


def _state_machine(value: Any, profiles: set[str]) -> None:
    value = exact(value, {"state_surface", "states", "transitions"}, "invalid_state_machine")
    need(value["state_surface"] == "kanban", "invalid_state_machine")
    states = set(string_list(value["states"], "invalid_state_machine"))
    need(set(REQUIRED_STATES) <= states, "invalid_state_machine")
    transitions = value["transitions"]
    need(isinstance(transitions, list) and transitions, "invalid_state_machine")
    edges: set[tuple[str, str]] = set()
    for transition in transitions:
        transition = exact(transition, {"from", "to", "event", "guard", "owner", "surface"},
                           "invalid_state_machine")
        source = text(transition["from"], "invalid_state_machine")
        target = text(transition["to"], "invalid_state_machine")
        need(source in states and target in states and transition["surface"] == "kanban",
             "invalid_state_machine")
        text(transition["event"], "invalid_state_machine")
        text(transition["guard"], "invalid_state_machine")
        need(transition["owner"] in profiles, "invalid_state_machine")
        edges.add((source, target))
    need(set(NORMAL_EDGES) <= edges, "incomplete_ooda_state_machine")


def _fanout(value: Any, profiles: set[str], decision_profile: str) -> None:
    value = exact(value, {"independent", "common_evidence_cutoff", "common_reward_dimensions",
                          "max_parallel", "fan_in", "evaluator_profile", "meeting_triggers"},
                  "invalid_fanout")
    need(value["independent"] is True and value["common_evidence_cutoff"] is True
         and value["common_reward_dimensions"] is True and value["fan_in"] == "group-chat",
         "invalid_fanout")
    need(isinstance(value["max_parallel"], int) and not isinstance(value["max_parallel"], bool)
         and 1 <= value["max_parallel"] <= 6, "invalid_fanout")
    need(value["evaluator_profile"] in profiles and value["evaluator_profile"] != decision_profile,
         "non_independent_evaluator")
    need(MEETING_TRIGGERS <= set(string_list(value["meeting_triggers"], "invalid_fanout")),
         "invalid_fanout")


def _persistence(value: Any) -> None:
    value = exact(value, {"internal_artifacts", "allowed", "mission_deliverable_requires_operator_locator",
                          "retain_chain_of_thought"}, "invalid_persistence")
    allowed = set(string_list(value["allowed"], "invalid_persistence"))
    required = {"configuration", "skill", "tool", "mission-deliverable", "native-state",
                "convergence-ledger"}
    need(value["internal_artifacts"] == "forbidden" and allowed == required
         and value["mission_deliverable_requires_operator_locator"] is True
         and value["retain_chain_of_thought"] is False, "invalid_persistence")


def _runtime_contract(instance: Path, path: Path, expected_version: str) -> tuple[dict[str, str], list[str]]:
    regular(path, "unsafe_runtime_contract")
    try:
        contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise Denied("invalid_runtime_contract") from exc
    contract = exact(contract, {"schema", "map_version", "stage", "mission", "personas",
                                "surfaces", "state_machine", "fanout", "persistence"},
                     "invalid_runtime_contract")
    need(contract["schema"] == 1 and contract["map_version"] == expected_version
         and contract["stage"] == "FORMATION_PASSED", "invalid_runtime_contract")
    mission = exact(contract["mission"], {"id", "source"}, "invalid_runtime_contract")
    text(mission["id"], "invalid_runtime_contract")
    locator(mission["source"], "source", "invalid_runtime_contract")
    roles, profile_paths = _profiles(instance, contract["personas"])
    _, room_keys = _surfaces(instance, contract["surfaces"])
    decision = next(row["profile"] for row in contract["personas"] if "decision" in row["functions"])
    _state_machine(contract["state_machine"], set(profile_paths))
    _fanout(contract["fanout"], set(profile_paths), decision)
    _persistence(contract["persistence"])
    shared = instance / "hermes-data/fleet-skills"
    for skill in ("fleet-organism-design", "fleet-convergence-learning"):
        regular(shared / skill / "SKILL.md", "missing_shared_skill")
    for profile_path in profile_paths.values():
        try:
            config = yaml.safe_load((profile_path / "config.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise Denied("invalid_profile_config") from exc
        dirs = ((config.get("skills") or {}).get("external_dirs") if isinstance(config, dict) else None)
        need(isinstance(dirs, list) and "/opt/data/fleet-skills" in dirs,
             "missing_shared_skill_wiring")
        jobs = fleet_doctor._jobs(profile_path / "cron/jobs.json")
        for job in jobs:
            active = (job.get("enabled", True) is not False
                      and job.get("state") not in {"paused", "completed"} and not job.get("paused_at"))
            if active:
                status, _ = fleet_doctor._artifact_culture(job, profile_path)
                need(status != "FAIL", "artifact_job_active")
    return roles, room_keys


def _rooms(instance: Path, roles: dict[str, str], room_keys: list[str], connection: dict) -> None:
    profile = regular(instance / "hermes-data/profile.yaml", "invalid_room_registry")
    try:
        data = yaml.safe_load(profile.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise Denied("invalid_room_registry") from exc
    ui = data.get("ui_meta") if isinstance(data, dict) else None
    registry = ui.get("hermes-bots-groups") if isinstance(ui, dict) else None
    rooms = registry.get("rooms") if isinstance(registry, dict) else None
    need(isinstance(rooms, dict), "invalid_room_registry")
    expected_profiles = set(roles.values())
    for key in room_keys:
        room = rooms.get(key)
        need(isinstance(room, dict) and not room.get("tombstone"), "invalid_room_registry")
        members = room.get("members")
        need(isinstance(members, list) and len(members) == 6, "invalid_room_registry")
        seen: set[str] = set()
        for member in members:
            need(isinstance(member, dict) and member.get("connectionId") == connection["id"]
                 and member.get("connectionKind") == connection["kind"]
                 and member.get("connectionLabel") == connection["label"], "invalid_room_registry")
            name = member.get("name")
            need(isinstance(name, str) and name in expected_profiles and name not in seen,
                 "invalid_room_registry")
            seen.add(name)
        need(seen == expected_profiles, "invalid_room_registry")


def _event_map(value: Any, roles: set[str], kind: str, code: str) -> dict[str, str]:
    need(isinstance(value, dict) and set(value) == roles, code)
    return {role: locator(item, kind, code) for role, item in value.items()}


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _accepted(instance: Path, scope: str, now: datetime, max_age_hours: float) -> None:
    receipt_path = regular(instance / RECEIPT, "unsafe_admission_ledger", private=True)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"),
                             object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, ValueError) as exc:
        raise Denied("invalid_admission_ledger") from exc
    receipt = exact(receipt, {"schema", "instance", "scope", "accepted_at", "map_version",
                              "contract", "member_connection", "operator_declaration",
                              "client_witness", "independent_assessor", "native_events"},
                    "invalid_admission_ledger")
    need(receipt["schema"] == 2 and receipt["instance"] == instance.name
         and receipt["scope"] == scope == SCOPE, "invalid_scope_or_instance")
    accepted_at = timestamp(receipt["accepted_at"], "invalid_time")
    need(accepted_at <= now and (now - accepted_at).total_seconds() <= max_age_hours * 3600,
         "stale_admission")
    version = text(receipt["map_version"], "invalid_runtime_contract")
    contract_path = safe_relative(instance, receipt["contract"], "unsafe_runtime_contract")
    roles, room_keys = _runtime_contract(instance, contract_path, version)

    connection = exact(receipt["member_connection"], {"id", "kind", "label"},
                       "invalid_connection")
    text(connection["id"], "invalid_connection")
    need(connection["kind"] == "remote", "invalid_connection")
    text(connection["label"], "invalid_connection")
    _rooms(instance, roles, room_keys, connection)

    operator = exact(receipt["operator_declaration"], {"name", "signed_at", "statement"},
                     "invalid_operator_declaration")
    operator_name = text(operator["name"], "invalid_operator_declaration")
    signed = timestamp(operator["signed_at"], "invalid_operator_declaration")
    need("mission-buildout only" in text(operator["statement"], "invalid_operator_declaration")
         and signed <= accepted_at, "invalid_operator_declaration")

    witness = exact(receipt["client_witness"], {"observer", "observed_at", "physically_seen",
                                                "room_keys", "post_id", "reply_id", "reply_profile"},
                    "invalid_client_witness")
    need(witness["observer"] == operator_name and witness["physically_seen"] is True
         and set(string_list(witness["room_keys"], "invalid_client_witness")) == set(room_keys)
         and witness["reply_profile"] in set(roles.values()), "invalid_client_witness")
    observed = timestamp(witness["observed_at"], "invalid_client_witness")
    text(witness["post_id"], "invalid_client_witness")
    text(witness["reply_id"], "invalid_client_witness")
    need(observed <= signed, "invalid_client_witness")

    assessor = exact(receipt["independent_assessor"], {"name", "reviewed_at", "finding"},
                     "invalid_assessor")
    need(text(assessor["name"], "invalid_assessor") != operator_name
         and assessor["finding"] == "pass", "invalid_assessor")
    reviewed = timestamp(assessor["reviewed_at"], "invalid_assessor")
    need(observed <= reviewed <= signed, "invalid_assessor")

    events = exact(receipt["native_events"], {"role_drills", "schedule_runs", "handoffs",
                                                     "room_post", "room_reply", "stop_test",
                                                     "recovery_test", "independent_assessment",
                                                     "convergence_recall", "retry_guard"},
                   "invalid_native_events")
    _event_map(events["role_drills"], set(roles), "kanban", "invalid_native_events")
    _event_map(events["schedule_runs"], set(roles), "cron", "invalid_native_events")
    handoffs = string_list(events["handoffs"], "invalid_native_events")
    need(all(LOCATORS["kanban"].fullmatch(item) for item in handoffs), "invalid_native_events")
    for key, kind in (("room_post", "room"), ("room_reply", "room"),
                      ("stop_test", "runtime"), ("recovery_test", "runtime"),
                      ("independent_assessment", "kanban"),
                      ("convergence_recall", "ledger"), ("retry_guard", "ledger")):
        locator(events[key], kind, "invalid_native_events")


def inspect(instance_dir: Path, scope: str = SCOPE, *, now: datetime | None = None,
            max_age_hours: float = 24.0) -> tuple[dict, int]:
    result = {"schema": 2, "read_only": True, "native_enforced": False,
              "live_client_verified": False, "native_event_locators_resolved": False,
              "operator_attested": False, "admitted": False, "scope": scope}
    try:
        instance = Path(instance_dir)
        info = instance.lstat()
        need(instance.is_dir() and not instance.is_symlink() and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) & 0o077 == 0, "unsafe_instance")
        need(isinstance(max_age_hours, (int, float)) and not isinstance(max_age_hours, bool)
             and 0 < max_age_hours <= 168, "invalid_max_age")
        _accepted(instance, scope, now or datetime.now(timezone.utc), float(max_age_hours))
    except (Denied, OSError) as exc:
        result.update(status="denied", reason=str(exc))
        return result, 1
    result.update(status="accepted_operator_attested_native_references",
                  operator_attested=True, admitted=True)
    return result, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-dir", type=Path, required=True)
    parser.add_argument("--scope", default=SCOPE, choices=[SCOPE])
    parser.add_argument("--max-age-hours", type=float, default=24.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, rc = inspect(args.instance_dir, args.scope, max_age_hours=args.max_age_hours)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Formation admission: {result['status']} ({result.get('reason', 'operator-attested')})")
        print("Native event locators and human identity are not resolved by this read-only check.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
