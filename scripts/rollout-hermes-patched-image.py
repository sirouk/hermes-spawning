#!/usr/bin/env python3
"""Read-only rollout preflight; produce one-fleet-at-a-time image plan.

This tool never edits instances, runs Compose, or recreates containers. See
``docs/patched-hermes-image.md`` for the manual staged apply and rollback.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hermes_gateway_layer", ROOT / "scripts/build-hermes-patched-image.py")
if spec is None or spec.loader is None:
    raise SystemExit("Cannot load gateway image builder")
layer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = layer
spec.loader.exec_module(layer)


def control_values(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise layer.PreflightError(f"Unsafe or missing instance control: {path}")
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in result:
            raise layer.PreflightError(f"Unsafe or duplicate control key in {path}")
        result[key] = value
    return result


def instance_dir(project: str) -> Path:
    name = project.removeprefix("hermes-")
    if not name or not re.fullmatch("[a-z0-9-]+", name):
        raise layer.PreflightError("Unsafe project name")
    path = ROOT / "instances" / name
    if path.is_symlink() or not path.is_dir() or path.resolve().parent != (ROOT / "instances").resolve():
        raise layer.PreflightError(f"Unsafe instance path {path}")
    return path


def validate_control(entry: dict) -> dict:
    path = instance_dir(entry["project"])
    control = path / "control.env"
    values = control_values(control)
    if values.get("COMPOSE_PROJECT_NAME") != entry["project"]:
        raise layer.PreflightError(f"Project mismatch in {control}")
    if values.get("HERMES_IMAGE") != "nousresearch/hermes-agent:latest":
        raise layer.PreflightError(f"Expected original official image tag in {control}")
    if values.get("HERMES_DATA_DIR") != str(path / "hermes-data"):
        raise layer.PreflightError(f"Unexpected instance data path in {control}")
    return {"instance": str(path), "control": str(control), "old_image": values["HERMES_IMAGE"]}


def rollout_plan() -> dict:
    patch, entries, sources = layer.plan()
    image_to_source = {entry["base_image"]: layer.patch_source(sources[entry["base_image"]], patch)
                       for entry in entries}
    # Every running base variant must have a prebuilt, verified candidate.
    for image in sorted(image_to_source):
        layer.verify_built_image(layer.tag_for(image, patch.digest), image_to_source[image], image, patch)
    for entry in entries:
        entry.update(validate_control(entry))
    return {"patch_sha256": patch.digest, "fleet": entries,
            "staging_hint": "Apply ONE chosen fleet, smoke and rollback-test before any other fleet. See docs/patched-hermes-image.md"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet", help="show just one fleet (all fleets are still preflight checked)")
    args = parser.parse_args()
    try:
        plan = rollout_plan()
        if args.fleet:
            matches = [entry for entry in plan["fleet"] if entry["project"] == f"hermes-{args.fleet}"]
            if len(matches) != 1:
                raise layer.PreflightError(f"Unknown fleet: {args.fleet}")
            plan["fleet"] = matches
        print(json.dumps(plan, indent=2))
        print("READ-ONLY: no control.env, container, or data changed.", file=sys.stderr)
    except (layer.PreflightError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
