#!/usr/bin/env python3
"""Recoverably strip active Hermes cognition and paperwork from stopped fleets.

Dry-run is the default. --apply moves exact cognition/runtime targets to one
host-side backup and installs only the repository's fleet skills, a FORMING
SOUL, and empty cron manifests. Credentials, config.yaml, profile.yaml,
connection identity, Tailscale state, and mission/source repositories are not
modified. Fleets are not started.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Iterable


PROFILE_DIRS = ("memories", "memory", "sessions", "state", "plans",
                "pending_messages", "cron", "skills", "scripts", "workspace",
                "terminal-sessions", "gpt-threads", "pastes", "reports",
                "handoff", "formation", "cache/scratch")
PROFILE_FILES = ("SOUL.md", "MEMORY.md", ".skills_prompt_snapshot.json")
DB_PREFIXES = ("state.db", "response_store.db", "runs_idempotency.db",
               "projects.db", "shared-state.db", "kanban.db")
ROOT_DIRS = ("kanban", "fleet-skills", ".curator_backups", "fleet-archives",
             "runtime-overlay", "tmpwork")
KNOWN_GENERATED_RELATIVES = (
    "hermes-data/tao-fleet/desk-six/cycles",
    "hermes-data/formation-evidence",
)
SOUL_START = "<!-- FLEET_SOUL_CORE:START -->"
SOUL_END = "<!-- FLEET_SOUL_CORE:END -->"
FORMING_SUFFIX = """# FORMING — mission work disabled

You are a dormant member of a Hermes fleet being formed from the canonical
`fleet-organism-design` skill. Load that skill and its Fleet Seed before doing
anything else. Observe live authoritative sources through tools, use Kanban for
flow and handoff, Group Chat for deliberation, and `fleet-convergence-learning`
for sparse source-linked experience. Do not perform mission work until the
native formation gate passes. Do not create cycle reports, role packets,
handoff files, close files, checkpoint files, or retro essays.
"""


class ResetError(RuntimeError):
    pass


def _instances(base: Path, requested: list[str], all_instances: bool) -> list[Path]:
    if not base.is_dir():
        raise ResetError(f"instances directory missing: {base}")
    if all_instances:
        result = sorted(path for path in base.iterdir()
                        if path.is_dir() and (path / "control.env").is_file())
    else:
        if not requested:
            raise ResetError("select --all or at least one --instance")
        result = []
        for name in requested:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
                raise ResetError(f"unsafe instance name: {name}")
            path = base / name
            if not path.is_dir() or not (path / "control.env").is_file():
                raise ResetError(f"instance missing: {name}")
            result.append(path)
    if not result:
        raise ResetError("no instances selected")
    return result


def _project(instance: Path) -> str:
    for line in (instance / "control.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("COMPOSE_PROJECT_NAME="):
            value = line.partition("=")[2].strip()
            if re.fullmatch(r"[A-Za-z0-9_.-]+", value):
                return value
    raise ResetError(f"safe COMPOSE_PROJECT_NAME missing: {instance.name}")


def _running_containers(project: str) -> list[str]:
    try:
        completed = subprocess.run(
            ["docker", "ps", "--filter", f"label=com.docker.compose.project={project}",
             "--format", "{{.ID}} {{.Names}}"], text=True, capture_output=True,
            timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ResetError("cannot verify Docker stop state") from exc
    if completed.returncode != 0:
        raise ResetError("cannot verify Docker stop state")
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _profiles(home: Path) -> list[Path]:
    result = [home]
    directory = home / "profiles"
    if directory.is_dir():
        result.extend(sorted(path for path in directory.iterdir()
                             if path.is_dir() and not path.is_symlink()
                             and (path / "config.yaml").is_file()))
    return result


def _targets(instance: Path) -> list[Path]:
    home = instance / "hermes-data"
    candidates: set[Path] = set()
    for profile in _profiles(home):
        candidates.update(profile / name for name in PROFILE_DIRS)
        candidates.update(profile / name for name in PROFILE_FILES)
        for child in profile.iterdir():
            if child.is_file() and any(child.name.startswith(prefix) for prefix in DB_PREFIXES):
                candidates.add(child)
    candidates.update(home / name for name in ROOT_DIRS)
    candidates.update(instance / relative for relative in KNOWN_GENERATED_RELATIVES)
    candidates.add(instance / "formation-admission.json")
    candidates.add(home / "fleet-runtime.yaml")
    existing = sorted((path for path in candidates if path.exists() or path.is_symlink()),
                      key=lambda path: (len(path.parts), str(path)))
    selected: list[Path] = []
    for path in existing:
        if not any(parent == path or parent in path.parents for parent in selected):
            selected.append(path)
    for path in selected:
        try:
            path.relative_to(instance)
        except ValueError as exc:
            raise ResetError(f"target escapes instance: {path}") from exc
    return selected


def _destination(backup: Path, instance: Path, target: Path) -> Path:
    return backup / instance.name / target.relative_to(instance)


def _copy_skills(repo: Path, home: Path) -> None:
    source = repo / "skills"
    target = home / "fleet-skills"
    if target.exists():
        raise ResetError(f"fleet-skills unexpectedly exists after reset: {target}")
    target.mkdir(parents=True, mode=0o755)
    for skill in sorted(source.iterdir()):
        if skill.is_dir() and (skill / "SKILL.md").is_file():
            shutil.copytree(skill, target / skill.name)


def _canonical_soul(repo: Path) -> str:
    seed = repo / "skills/fleet-organism-design/references/fleet-seed.md"
    try:
        value = seed.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ResetError("canonical Fleet Seed cannot be read") from exc
    if value.count(SOUL_START) != 1 or value.count(SOUL_END) != 1:
        raise ResetError("canonical Fleet Seed has no unique SOUL inheritance section")
    start = value.index(SOUL_START)
    end = value.index(SOUL_END, start) + len(SOUL_END)
    return value[start:end] + "\n\n" + FORMING_SUFFIX


def _container_owner(instance: Path) -> tuple[int, int]:
    env = instance / "control.env"
    uid = gid = None
    try:
        lines = env.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ResetError(f"cannot read container owner env {env}: {exc}") from exc
    for line in lines:
        if line.startswith("HERMES_UID="):
            uid = int(line.split("=", 1)[1].strip())
        elif line.startswith("HERMES_GID="):
            gid = int(line.split("=", 1)[1].strip())
    if uid is None:
        raise ResetError(f"control.env lacks HERMES_UID: {env}")
    return uid, (gid if gid is not None else uid)


def _chown_container(path: Path, owner: tuple[int, int]) -> None:
    if os.geteuid() != 0:
        return  # non-root cannot chown; container entrypoint repairs ownership
    os.chown(path, owner[0], owner[1])


def _bootstrap(instance: Path, repo: Path) -> list[str]:
    home = instance / "hermes-data"
    _copy_skills(repo, home)
    owner = _container_owner(instance)
    soul_value = _canonical_soul(repo)
    written: list[str] = []
    for profile in _profiles(home):
        soul = profile / "SOUL.md"
        soul.write_text(soul_value, encoding="utf-8")
        soul.chmod(0o600)
        cron = profile / "cron"
        cron.mkdir(mode=0o700, exist_ok=True)
        jobs = cron / "jobs.json"
        jobs.write_text('{"jobs":[]}\n', encoding="utf-8")
        jobs.chmod(0o600)
        for path in (soul, cron, jobs):
            _chown_container(path, owner)
        written.extend([str(soul.relative_to(instance)), str(jobs.relative_to(instance))])
    skills_root = home / "fleet-skills"
    _chown_container(skills_root, owner)
    for root, dirs, files in os.walk(skills_root):
        _chown_container(Path(root), owner)
        for name in dirs + files:
            _chown_container(Path(root) / name, owner)
    written.append(str(skills_root.relative_to(instance)))
    return written


def execute(instances: Iterable[Path], *, repo: Path, backup: Path, apply: bool) -> dict:
    instances = list(instances)
    plans = []
    for instance in instances:
        running = _running_containers(_project(instance))
        if running:
            raise ResetError(f"instance still running: {instance.name}: {', '.join(running)}")
        targets = _targets(instance)
        plans.append({"instance": instance.name,
                      "move": [str(path.relative_to(instance)) for path in targets]})
    report = {"schema": 1, "applied": apply, "backup": str(backup), "instances": plans,
              "preserved": ["control.env", ".env/auth/vault", "tailscale-state",
                            "config.yaml", "profile.yaml/room identity",
                            "mission and operator source repositories"],
              "restarted": False}
    if not apply:
        return report
    if backup.exists():
        raise ResetError(f"backup target already exists: {backup}")
    backup.mkdir(parents=True, mode=0o700)
    try:
        for plan, instance in zip(plans, instances):
            for relative in plan["move"]:
                source = instance / relative
                destination = _destination(backup, instance, source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
            plan["installed"] = _bootstrap(instance, repo)
        manifest = backup / "reset-ledger.json"
        manifest.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest.chmod(0o600)
    except Exception as exc:
        raise ResetError(f"reset interrupted; preserve backup and reconcile manually: {exc}") from exc
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instances-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "instances")
    parser.add_argument("--instance", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--summary", action="store_true",
                        help="print counts instead of every exact target")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.all and args.instance:
            raise ResetError("use --all or --instance, not both")
        repo = Path(__file__).resolve().parents[1]
        selected = _instances(args.instances_dir.resolve(), args.instance, args.all)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = (args.backup_dir or (repo / ".cache" / f"fleet-cognition-reset-{stamp}")).resolve()
        report = execute(selected, repo=repo, backup=backup, apply=args.apply)
        output = report
        if args.summary:
            output = dict(report)
            output["instances"] = [
                {"instance": item["instance"], "move_count": len(item["move"]),
                 **({"installed_count": len(item["installed"])} if "installed" in item else {})}
                for item in report["instances"]]
        print(json.dumps(output, sort_keys=True))
        return 0
    except (ResetError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
