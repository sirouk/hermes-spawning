#!/usr/bin/env python3
"""Fail-closed, image-layer-only Hermes gateway patch builder.

Dry-run is default. No upstream checkout, instance data, or running container is written.
See docs/patched-hermes-image.md before using --build.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/hermes-desktop-group-projection-192k.patch"
SOURCE = "/opt/hermes/tui_gateway/methods_profiles.py"
PATCH_PATH = "tui_gateway/methods_profiles.py"
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}\Z")
REVISION = re.compile(r"[a-f0-9]{40}\Z")
TAG_PREFIX = "local/hermes-gateway-ui-meta"


class PreflightError(RuntimeError):
    pass


def run(*args: str, allow_missing: bool = False) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode:
        if allow_missing:
            return ""
        raise PreflightError(f"{' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def json_command(*args: str) -> list[dict]:
    try:
        result = json.loads(run(*args))
    except ValueError as exc:
        raise PreflightError(f"Invalid Docker JSON from {args!r}") from exc
    if not isinstance(result, list):
        raise PreflightError(f"Expected Docker JSON array from {args!r}")
    return result


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Patch:
    digest: str
    before: bytes
    after: bytes


def read_patch(path: Path = PATCH) -> Patch:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    marker = f"diff --git a/{PATCH_PATH} b/{PATCH_PATH}\n"
    if text.count(marker) != 1:
        raise PreflightError("Expected exactly one gateway-file section in tracked patch")
    section = text.split(marker, 1)[1].split("\ndiff --git ", 1)[0]
    if not section.startswith("index ") or f"--- a/{PATCH_PATH}\n+++ b/{PATCH_PATH}\n" not in section:
        raise PreflightError("Unexpected gateway patch header")
    chunks = re.split(r"(?m)^@@[^\n]*@@[^\n]*\n", section)
    if len(chunks) != 2:
        raise PreflightError("Expected one bounded gateway function hunk")
    lines = chunks[1].splitlines(keepends=True)
    before: list[str] = []
    after: list[str] = []
    minus: list[str] = []
    plus: list[str] = []
    for line in lines:
        if line.startswith(" "):
            before.append(line[1:]); after.append(line[1:])
        elif line.startswith("-"):
            before.append(line[1:]); minus.append(line[1:])
        elif line.startswith("+"):
            after.append(line[1:]); plus.append(line[1:])
        elif line == "\\ No newline at end of file\n":
            raise PreflightError("Gateway source must end in a newline")
        else:
            raise PreflightError("Unexpected gateway patch hunk content")
    # Only these two audited changes are permitted in a gateway image.
    if minus != [
        '    """Merge ``params["ui_meta"]`` key-wise into profile.yaml (None deletes). 64KB cap (rides\n',
        '        if len(json.dumps(incoming)) > 65536:\n',
    ] or plus != [
        '    """Merge ``params["ui_meta"]`` key-wise into profile.yaml (None deletes). 256KB cap (rides\n',
        '        if len(json.dumps(incoming)) > 262144:\n',
    ]:
        raise PreflightError("Gateway patch changed outside audited 64KiB -> 256KiB cap")
    old = "".join(before).encode()
    new = "".join(after).encode()
    if not old.startswith(b"\n\ndef _configure_ui_meta(") or len(old) < 400:
        raise PreflightError("Gateway hunk must be anchored to _configure_ui_meta")
    return Patch(sha(raw), old, new)


def patch_source(source: bytes, patch: Patch) -> bytes:
    if source.count(patch.before) != 1 or patch.after in source:
        raise PreflightError("Gateway hunk is absent, already patched, or ambiguous")
    result = source.replace(patch.before, patch.after, 1)
    if result.count(b"def _configure_ui_meta(") != 1:
        raise PreflightError("Gateway function is missing or ambiguous")
    return result


def cp_source(container: str) -> tuple[bytes, tarfile.TarInfo]:
    proc = subprocess.run(["docker", "cp", f"{container}:{SOURCE}", "-"], capture_output=True, check=False)
    if proc.returncode:
        raise PreflightError(f"Cannot read gateway file from {container}: {proc.stderr.decode(errors='replace')}")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as archive:
        members = archive.getmembers()
        if len(members) != 1 or not members[0].isfile() or members[0].size > 2_000_000:
            raise PreflightError("Unexpected gateway file in docker cp archive")
        payload = archive.extractfile(members[0])
        if payload is None:
            raise PreflightError("Cannot extract gateway file")
        return payload.read(), members[0]


def fleet_containers() -> list[dict]:
    names = run("docker", "ps", "--filter", "label=com.docker.compose.service=hermes", "--format", "{{.ID}}").splitlines()
    if not names:
        raise PreflightError("No running Compose hermes containers")
    containers = json_command("docker", "container", "inspect", *names)
    if len(containers) != len(names):
        raise PreflightError("Container inspection incomplete")
    projects: set[str] = set()
    for container in containers:
        labels = container.get("Config", {}).get("Labels", {})
        project = labels.get("com.docker.compose.project", "")
        if not re.fullmatch(r"hermes-[a-z0-9-]+", project) or project in projects:
            raise PreflightError("Missing/duplicate/unsafe Compose project")
        projects.add(project)
        if labels.get("com.docker.compose.service") != "hermes":
            raise PreflightError("Container is not Compose hermes service")
        if not container.get("State", {}).get("Running"):
            raise PreflightError("Gateway container stopped during preflight")
    return sorted(containers, key=lambda c: c["Config"]["Labels"]["com.docker.compose.project"])


def inspect_images(ids: set[str]) -> dict[str, dict]:
    if not ids or any(not IMAGE_ID.fullmatch(image) for image in ids):
        raise PreflightError("Expected explicit immutable sha256 image IDs")
    images = json_command("docker", "image", "inspect", *sorted(ids))
    result = {item.get("Id"): item for item in images}
    if set(result) != ids:
        raise PreflightError("Image inspection did not return the exact requested IDs")
    for image in images:
        revision = image.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision", "")
        if not REVISION.fullmatch(revision):
            raise PreflightError("Base image is missing a 40-character source revision label")
        if image.get("Os") != "linux":
            raise PreflightError("Base image is not Linux")
    return result


def verify_base_source(image: str, expected: bytes) -> None:
    # Running containers can differ from their immutable base even with a clean
    # gateway-file diff when image paths are overlaid by volumes or imports.
    created = run("docker", "create", "--network", "none", "--entrypoint", "/bin/true", image).strip()
    try:
        actual, metadata = cp_source(created)
        if actual != expected or (metadata.uid, metadata.gid, metadata.mode & 0o7777) != (0, 0, 0o644):
            raise PreflightError("Gateway source in immutable base differs from running source")
    finally:
        run("docker", "rm", "-f", created)


def verify_container(container: dict, patch: Patch) -> tuple[bytes, bytes]:
    name = container["Name"].lstrip("/")
    changed = run("docker", "diff", name).splitlines()
    if any(line[2:] == SOURCE or line[2:].startswith(SOURCE + "/") for line in changed):
        raise PreflightError(f"{name} modified gateway source in its writable layer")
    if any(m.get("Destination") == SOURCE or SOURCE.startswith(m.get("Destination", "") + "/")
           for m in container.get("Mounts", [])):
        raise PreflightError(f"{name} mounts over gateway source")
    data_mounts = [m for m in container.get("Mounts", []) if m.get("Destination") == "/opt/data"]
    if len(data_mounts) != 1 or data_mounts[0].get("Type") != "bind":
        raise PreflightError(f"{name} has unexpected data mount")
    source, metadata = cp_source(name)
    if (metadata.uid, metadata.gid, metadata.mode & 0o7777) != (0, 0, 0o644):
        raise PreflightError(f"{name} gateway file permissions/owner differ from base")
    return source, patch_source(source, patch)


def plan() -> tuple[Patch, list[dict], dict[str, bytes]]:
    patch = read_patch()
    containers = fleet_containers()
    images = inspect_images({c["Image"] for c in containers})
    sources: dict[str, bytes] = {}
    result: list[dict] = []
    for container in containers:
        image = container["Image"]
        original, patched = verify_container(container, patch)
        if image in sources and sources[image] != original:
            raise PreflightError("Containers on same image ID have different gateway source")
        sources[image] = original
        result.append({
            "project": container["Config"]["Labels"]["com.docker.compose.project"],
            "container": container["Name"].lstrip("/"),
            "base_image": image,
            "base_revision": images[image]["Config"]["Labels"]["org.opencontainers.image.revision"],
            "base_source_sha256": sha(original),
            "patched_source_sha256": sha(patched),
            "patched_tag": tag_for(image, patch.digest),
        })
    for image, source in sources.items():
        verify_base_source(image, source)
    return patch, result, sources


def tag_for(base: str, patch_digest: str) -> str:
    return f"{TAG_PREFIX}:{base[7:23]}-{patch_digest[:16]}"


def verify_built_image(image: str, expected_source: bytes, base_id: str, patch: Patch) -> None:
    items = json_command("docker", "image", "inspect", image)
    if len(items) != 1:
        raise PreflightError("Built image inspection incomplete")
    labels = items[0].get("Config", {}).get("Labels", {})
    if labels.get("org.hermes-spawning.base-image") != base_id or labels.get("org.hermes-spawning.patch-sha256") != patch.digest:
        raise PreflightError("Built image missing exact base/patch labels")
    created = run("docker", "create", "--network", "none", "--entrypoint", "/bin/true", image).strip()
    try:
        actual, meta = cp_source(created)
        if actual != expected_source or (meta.uid, meta.gid, meta.mode & 0o7777) != (0, 0, 0o644):
            raise PreflightError("Built gateway source does not match audited patch")
    finally:
        run("docker", "rm", "-f", created)


def build(patch: Patch, entries: list[dict], sources: dict[str, bytes]) -> None:
    if sha(PATCH.read_bytes()) != patch.digest:
        raise PreflightError("Tracked patch changed after preflight; restart")
    for image in sorted(sources):
        tag = tag_for(image, patch.digest)
        expected = patch_source(sources[image], patch)
        # Refuse to overwrite an existing tag, even if it looks plausible.
        if run("docker", "image", "inspect", tag, allow_missing=True):
            raise PreflightError(f"Refusing to overwrite existing local tag {tag}")
        with tempfile.TemporaryDirectory(prefix="hermes-gateway-layer-") as dirname:
            context = Path(dirname)
            (context / "methods_profiles.py").write_bytes(expected)
            (context / "Dockerfile").write_text(
                f"FROM {image}\n"
                "COPY --chown=0:0 --chmod=0644 methods_profiles.py /opt/hermes/tui_gateway/methods_profiles.py\n"
                f'LABEL org.hermes-spawning.base-image="{image}" '
                f'org.hermes-spawning.patch-sha256="{patch.digest}"\n'
            )
            run("docker", "build", "--pull=false", "--network", "none", "-t", tag, str(context))
        verify_built_image(tag, expected, image, patch)
        print(f"Built and verified {tag} (base {image})", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="build locally only after full read-only preflight")
    args = parser.parse_args()
    try:
        patch, entries, sources = plan()
        print(json.dumps({"patch_sha256": patch.digest, "fleet": entries, "unique_bases": len(sources)}, indent=2))
        if args.build:
            build(patch, entries, sources)
        else:
            print("READ-ONLY PLAN; pass --build to create local images. No fleet rollout.", file=sys.stderr)
    except (PreflightError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
