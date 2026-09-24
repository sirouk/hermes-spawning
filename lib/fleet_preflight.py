#!/usr/bin/env python3
"""Read-only fleet readiness check. Never runs model turns, jobs, or MCP clients.

Host: fleet_preflight.py --instance-dir instances/NAME --repo . [--json]
Container: fleet_preflight.py --home /opt/data --runtime-root /opt/hermes
  --defaults FILE --skills-dir DIR --base-url URL --api-key-env KEY_VAR
The host wrapper reads Docker's current installed source via python -B, not an
image tag. Runtime evidence is produced separately by verify-fleet-runtime.py.
Evidence is a trusted local operator record, NOT a signed security attestation.
Missing, stale, or mismatched records fail readiness. MCP auth presence does
not prove token validity. Desktop registry readability does not prove delivery.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
sys.dont_write_bytecode = True

SCHEMA = 1

def file_hash(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"

def tree_hash(root, runtime=False):
    root = Path(root)
    if not root.is_dir():
        return "missing"
    digest = hashlib.sha256()
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in {".git", ".venv", "__pycache__", "node_modules"})
        for name in sorted(files):
            p = Path(directory) / name
            # Native skill_view updates only these skill-root bookkeeping files.
            # Keep manifests, arbitrary dotfiles, and nested skill assets bound.
            if not runtime and p.parent == root and name in {".usage.json", ".usage.json.lock"}:
                continue
            if runtime and p.suffix != ".py" and name not in {"pyproject.toml", "uv.lock"}:
                continue
            digest.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes() + b"\0")
            count += 1
    return digest.hexdigest() if count else "missing"

def runtime_fingerprint(root):
    root = Path(root)
    if not (root / "tools" / "terminal_tool.py").is_file():
        raise ValueError("installed Hermes source is missing")
    return tree_hash(root, runtime=True)

def external_skill_hashes(home, active, skills_dir):
    """Bind relevant external fleet roots only; never scan arbitrary external trees."""
    import yaml
    config = yaml.safe_load((active / "config.yaml").read_text()) if (active / "config.yaml").is_file() else {}
    config = config or {}
    directories = (config.get("skills") or {}).get("external_dirs") or []
    if not isinstance(directories, list):
        return {"invalid": "unverifiable"}
    names = sorted(p.parent.name for p in Path(skills_dir).glob("*/SKILL.md"))
    hashes = {}
    for directory in directories:
        if not isinstance(directory, str):
            hashes["invalid"] = "unverifiable"
            continue
        logical = Path(directory)
        try:
            relative = logical.relative_to("/opt/data")
            mapped = (home / relative).resolve()
            mapped.relative_to(home.resolve())
        except ValueError:
            hashes[directory] = "unverifiable"
            continue
        if not mapped.is_dir():
            hashes[directory] = "unverifiable"
            continue
        for name in names:
            target = mapped / name
            try:
                target.resolve().relative_to(home.resolve())
                hashes[directory + "/" + name] = tree_hash(target)
            except (ValueError, OSError):
                hashes[directory + "/" + name] = "unverifiable"
    return hashes

def binding(home, persona, defaults_path, skills_dir, runtime_sha256):
    home = Path(home)
    active = home if persona == "default" else home / "profiles" / persona
    skills = {"fleet": tree_hash(skills_dir), "installed": tree_hash(home / "fleet-skills"), "root": tree_hash(home / "skills"),
              "active": tree_hash(active / "skills"),
              "external": external_skill_hashes(home, active, skills_dir)}
    return {"config_sha256": file_hash(active / "config.yaml"),
            "env_sha256": file_hash(active / ".env"),
            "root_config_sha256": file_hash(home / "config.yaml"),
            "root_env_sha256": file_hash(home / ".env"),
            "defaults_sha256": file_hash(defaults_path),
            "skills_sha256": hashlib.sha256(json.dumps(skills, sort_keys=True).encode()).hexdigest(),
            "runtime_sha256": runtime_sha256}

def validate_evidence(record, expected, persona, skill_names, max_age=86400, now=None):
    errors = []
    if not isinstance(record, dict):
        return ["evidence must be an object"]
    if record.get("failed") is True:
        errors.append("qualification explicitly failed")
    if record.get("schema") != SCHEMA or record.get("persona") != persona:
        errors.append("schema/persona mismatch")
    if record.get("producer") != "verify-fleet-runtime" or record.get("scope") != "native-end-to-end":
        errors.append("native end-to-end producer/scope missing")
    if record.get("binding") != expected or not re.fullmatch(r"[0-9a-f]{64}", expected.get("runtime_sha256", "")):
        errors.append("config/skills/runtime binding mismatch or runtime unavailable")
    try:
        stamp = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
        age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
        if age < -60 or age > max_age:
            errors.append("evidence expired or future dated")
    except (KeyError, TypeError, ValueError):
        errors.append("invalid evidence timestamp")
    checks = record.get("checks") or {}
    if not isinstance(checks, dict):
        return errors + ["checks must be an object"]
    skills = checks.get("fleet_skills_visible") or {}
    if (not isinstance(skills, dict) or skills.get("ok") is not True
            or not isinstance(skills.get("names"), list)
            or not all(isinstance(n, str) for n in skills.get("names", []))
            or not all(name in skills.get("names", []) for name in skill_names) or not skills.get("call_ids")):
        errors.append("native fleet skill visibility not proven")
    approvals = checks.get("approvals") or {}
    if not isinstance(approvals, dict) or approvals.get("ok") is not True or approvals.get("mode") != "off" or approvals.get("pending") != 0:
        errors.append("runtime approvals off/zero pending not proven")
    for name in ("foreground_terminal", "cron_execute_code", "cron_dangerous_shell",
                 "single_query_dangerous_shell", "api_dangerous_shell"):
        result = checks.get(name) or {}
        if not isinstance(result, dict):
            result = {}
        expected_stdout = result.get("expected_stdout")
        if (result.get("ok") is not True or not result.get("session_id") or not result.get("call_id")
                or not isinstance(expected_stdout, str) or not expected_stdout
                or not isinstance(result.get("stdout"), str) or expected_stdout not in result["stdout"]
                or type(result.get("exit_code")) is not int or result["exit_code"] != 0):
            errors.append(name + ": actual tool stdout/exit not proven")
        if name.startswith("cron_") and (not result.get("job_id") or result.get("cleanup_ok") is not True):
            errors.append(name + ": completed job/cleanup evidence missing")
        if name != "cron_execute_code":
            arguments = result.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            timeout = arguments.get("timeout")
            if (arguments.get("background") is not False or arguments.get("heartbeat", 0) not in (None, 0)
                    or not isinstance(timeout, (int, float)) or not 0 < timeout <= 10):
                errors.append(name + ": not a valid foreground timeout<=10 call")
        classification = result.get("classification") or record.get("classification") or {}
        dangerous = result.get("classified_dangerous", classification.get("dangerous") if isinstance(classification, dict) else None)
        hardline = result.get("classified_hardline", classification.get("hardline") if isinstance(classification, dict) else None)
        if "dangerous_shell" in name and (dangerous is not True or hardline is not False):
            errors.append(name + ": safe approval-sensitive probe classification not proven")
    return errors

def read_env(path):
    """Parse assignment data, never source/evaluate shell code or substitutions."""
    values = {}
    path = Path(path)
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", line.strip())
        if not match:
            raise ValueError("unsupported env assignment")
        parts = shlex.split(match[2], comments=True)
        if len(parts) > 1 or any(c in match[2] for c in ("$", "`")):
            raise ValueError("env expansions are not evaluated")
        values[match[1]] = parts[0] if parts else ""
    return values

def installed_fingerprint(instance, repo, control):
    command = ["docker", "compose", "--project-name", control["COMPOSE_PROJECT_NAME"],
               "--env-file", str(instance / "control.env"), "-f", str(repo / "compose.yaml"),
               "exec", "-T", "hermes", "/opt/hermes/.venv/bin/python", "-B", "-",
               "--fingerprint-runtime", "/opt/hermes"]
    result = subprocess.run(command, input=Path(__file__).read_text(), text=True,
                            capture_output=True, timeout=30, check=False)
    digest = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("cannot read installed runtime fingerprint (container unavailable or source unreadable)")
    return digest

def check(args):
    import apply_stack as stack
    import yaml
    home = Path(args.home)
    if not home.is_dir():
        raise ValueError("instance home is missing")
    defaults = stack._load_yaml(args.defaults)
    args.base_url = args.base_url.rstrip("/")
    endpoint_path = (defaults.get("endpoint") or {}).get("path", "/v1")
    if not args.base_url.endswith(endpoint_path):
        args.base_url += endpoint_path
    rep = stack.Reporter(quiet=True)
    stack.process(str(home), defaults, args.base_url, args.api_key, args.launch_profile, "status", rep, policy_only=args.policy_only)
    # Do not print status issue values: old runtimes can include provider secrets.
    rows = []
    def row(persona, name, ok, detail):
        rows.append({"persona": persona, "check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
    for persona in stack.personas(str(home)):
        count = sum(1 for where, _ in rep.issues if where == persona or where.startswith(persona + "/"))
        row(persona, "stack", count == 0, "on-disk stack clean" if not count else f"{count} stack drift issue(s); run stack-status locally")
    other = [where for where, _ in rep.issues if not any(where == name or where.startswith(name + "/") for name in stack.personas(str(home)))]
    if other:
        row("*", "stack", False, f"{len(other)} instance stack drift issue(s)")
    fleet = sorted(p.parent.name for p in Path(args.skills_dir).glob("*/SKILL.md"))
    row("*", "fleet_skill_sources", bool(fleet), f"{len(fleet)} fleet skills")
    for persona in stack.personas(str(home)):
        active = home if persona == "default" else home / "profiles" / persona
        cfg = stack._load_yaml(active / "config.yaml")
        external = external_skill_hashes(home, active, args.skills_dir)
        row(persona, "external_skill_sources", "unverifiable" not in external.values(),
            "relevant external sources fingerprinted" if "unverifiable" not in external.values() else "unsupported evidence binding: external skill source is outside mounted home, missing, or unreadable (not a native visibility claim)")
        for name in fleet:
            installed = home / "fleet-skills" / name
            ok = tree_hash(installed) == tree_hash(Path(args.skills_dir) / name)
            for shadow in {active / "skills" / name}:
                if shadow.exists():
                    ok = ok and tree_hash(shadow) == tree_hash(installed)
            row(persona, "skill:" + name, ok, "file parity only; native visibility requires runtime evidence")
        for filename, key in (("MEMORY.md", "memory_char_limit"), ("USER.md", "user_char_limit")):
            path = active / "memories" / filename
            count = len(path.read_text()) if path.exists() else 0
            limit = (cfg.get("memory") or {}).get(key, 0)
            row(persona, "memory:" + filename, isinstance(limit, int) and limit > count,
                f"{count} characters; configured limit {limit}")
        pf = active / "profile.yaml"
        try:
            meta = yaml.safe_load(pf.read_text())
            if not isinstance(meta, dict):
                raise ValueError("profile metadata must be a mapping")
            ui = meta.get("ui_meta")
            registry = ui.get("hermes-bots-groups") if isinstance(ui, dict) else None
            # Empty local ui_meta is a readable named-profile isolation shadow.
            ok = isinstance(ui, dict) and (registry is None or isinstance(registry, dict))
            row(persona, "desktop_registry", ok, "registry readable" if isinstance(registry, dict) else "empty registry (room_count=0; no room delivery claim)" if ok else "registry missing/not ready")
        except (OSError, ValueError, yaml.YAMLError):
            row(persona, "desktop_registry", False, "profile metadata missing/unreadable")
        mcp = cfg.get("mcp_servers") or {}
        names = sorted(mcp) if isinstance(mcp, dict) else []
        auth = {}
        for name in names:
            spec = mcp[name] if isinstance(mcp[name], dict) else {}
            auth[name] = {"auth_status": "UNKNOWN", "configured_auth_fields": any(bool(spec.get(k)) for k in
                         ("headers", "env", "auth", "api_key", "token", "oauth"))}
        rows.append({"persona": persona, "check": "mcp", "status": "INFO", "servers": auth,
                     "detail": "configuration/auth-field presence only; env presence may not be auth; token validity unknown; no connection performed"})
        evidence = Path(args.evidence_dir or home / "fleet-preflight") / (persona + ".json")
        try:
            record = json.loads(evidence.read_text())
            expected = binding(home, persona, Path(args.defaults), Path(args.skills_dir), args.runtime_fingerprint or "")
            errors = validate_evidence(record, expected, persona, fleet, args.max_evidence_age)
        except (OSError, ValueError, TypeError):
            errors = ["runtime evidence missing/unreadable"]
        row(persona, "runtime_evidence", not errors, "; ".join(errors) if errors else "fresh native end-to-end evidence matches current files/runtime")
    return {"read_only": True, "ready": not any(r["status"] == "FAIL" for r in rows), "checks": rows}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fingerprint-runtime", type=Path)
    parser.add_argument("--instance-dir", type=Path)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--home")
    parser.add_argument("--defaults")
    parser.add_argument("--skills-dir")
    parser.add_argument("--launch-profile", default="default")
    parser.add_argument("--policy-only", "--preserve-model-stack", dest="policy_only", action="store_true")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-env", default="MODEL_STACK_API_KEY")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--runtime-fingerprint", default="")
    parser.add_argument("--evidence-dir")
    parser.add_argument("--max-evidence-age", type=int, default=86400)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.fingerprint_runtime:
            print(runtime_fingerprint(args.fingerprint_runtime))
            return 0
        args.defaults = args.defaults or str(args.repo / "stack-defaults.yaml")
        args.skills_dir = args.skills_dir or str(args.repo / "skills")
        args.api_key = args.api_key or os.environ.get(args.api_key_env, "")
        fingerprint_error = None
        if args.instance_dir:
            instance = args.instance_dir.resolve()
            if not (instance / "control.env").is_file():
                raise ValueError("instance control.env is missing")
            control = read_env(instance / "control.env")
            endpoint = read_env(args.repo / ".model-endpoint")
            args.home = control.get("HERMES_DATA_DIR", str(instance / "hermes-data"))
            args.launch_profile = control.get("HERMES_PROFILE", "default")
            args.policy_only = args.policy_only or control.get("STACK_MANAGED", "yes").lower() in {"no", "false", "0"}
            args.base_url = control.get("MODEL_STACK_BASE_URL") or endpoint.get("MODEL_STACK_BASE_URL", "")
            args.api_key = control.get("MODEL_STACK_API_KEY") or endpoint.get("MODEL_STACK_API_KEY", "")
            try:
                args.runtime_fingerprint = installed_fingerprint(instance, args.repo, control)
            except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
                fingerprint_error = "installed runtime unavailable; no runtime readiness claim"
                args.runtime_fingerprint = ""
        elif args.runtime_root:
            args.runtime_fingerprint = runtime_fingerprint(args.runtime_root)
        if not args.home or args.max_evidence_age <= 0:
            raise ValueError("--home/--instance-dir and positive evidence age required")
        result = check(args)
        if fingerprint_error:
            result["checks"].append({"persona": "*", "check": "installed_runtime", "status": "FAIL", "detail": fingerprint_error})
            result["ready"] = False
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for row in result["checks"]:
                print("{status} {persona}/{check}: {detail}".format(**row))
            print("READY" if result["ready"] else "NOT READY")
        return 0 if result["ready"] else 1
    except Exception as exc:
        # Never echo config parser/source lines: they can contain credentials.
        error = {"read_only": True, "ready": False, "error": "preflight input/read failure", "error_type": type(exc).__name__}
        print(json.dumps(error) if args.json else "FAIL preflight input/read failure (" + type(exc).__name__ + ")")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
