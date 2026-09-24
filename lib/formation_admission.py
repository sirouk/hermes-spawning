#!/usr/bin/env python3
"""Read-only, host-owned *operator-attested* crew formation admission snapshot.

    python3 lib/formation_admission.py --instance-dir DIR --scope mission-buildout --json

This is deliberately separate from advisory formation_status.py and never writes a
receipt or data. Exit 0 only for an accepted operator-attested crew snapshot;
exit 1 for every other state. It does NOT authenticate a human, probe a live
Desktop client, pause jobs, or guard Hermes shell/cron/direct execution. Keep
mission credentials and execution permission behind real operator-controlled
boundaries. A mutable agent-controlled artifact can be replaced after a read;
use a filesystem snapshot/stop writers when atomicity matters.

The operator creates DIR/formation-admission.json outside DIR/hermes-data. The
instance directory must be owned by the invoking uid and private (no group or
other bits). The receipt must be an owned, single-link, regular 0600 file.
No agent-written status, native formation declaration, or CLI option creates it.
Strict JSON schema v1 (unknown or duplicate keys fail):

{
 "schema": 1, "instance": "NAME", "mode": "crew", "scope": "mission-buildout",
 "accepted_at": "2026-09-23T12:00:00Z", "map_version": "v0",
 "roles": [{"role": "founder", "profile": "default"}, ...six distinct roles
           and six distinct profiles, exactly root + five native profiles...],
 "rooms": {"coordination": "id:<client_room_id>", "retrospective": "name:Retro"},
 "member_connection": {"id":"<exact Desktop registry connection id>", "kind":"remote",
                       "label":"<Desktop connection label>"},
 "operator_declaration": {"name": "human name", "signed_at": "...Z",
    "statement": "I accept the bound operating map and this crew for mission-buildout only"},
 "human_client_witness": {"observer": "human name", "observed_at": "...Z",
    "client": "Hermes Desktop", "physically_seen": true,
    "room_keys": ["name:Coordination", "name:Retro"],
    "post_id": "on-disk log id", "reply_id": "on-disk log id",
    "reply_profile": "native responding member profile",
    "seen_post_text": "visible post text", "seen_reply_text": "visible reply text"},
 "independent_auditor": {"name": "a different human", "reviewed_at": "...Z",
    "finding": "pass", "observations": "specific independent review"},
 "bindings": {"control_env": REF, "operating_map": REF,
    "fleet_skills": REF,
    "default.soul": REF, "default.config": REF, "default.profile": REF,
    "default.cron": REF, "default.skills": REF,
    "NAMED.soul": REF, "NAMED.config": REF, "NAMED.profile": REF,
    "NAMED.cron": REF, "NAMED.skills": REF, ...all five named profiles...},
 "evidence": {"cycle": REF, "client": REF, "audit": REF,
    "role.ROLE": REF, ...all six roles...}
}
REF is exactly {"path": "relative/path/under/instance", "sha256": "64 lowercase hex"}.
Only skill refs name directories; they hash a sorted recursive tree of file
paths, empty directories, and contents (see _tree_hash), excluding only five
known mutable telemetry sidecars at each profile skills/ root. Such usage
and curator state are NOT bound and need a separate audit when relevant.
All other refs name
single regular files. No absolute, parent/., symlink, special, or hardlinked
ref is allowed; a changed byte fails. The operating_map path is inside hermes-data. A canonical
FLEET_OPERATING_MAP.md has a Version: header matching the receipt near its
top; alternatively a JSON map has a matching map_version key. Other material
paths have fixed native locations: control.env; hermes-data/fleet-skills;
hermes-data/{SOUL.md,config.yaml,profile.yaml,cron/jobs.json,skills};
and corresponding hermes-data/profiles/NAME/... for named profiles. Thus root
and named SOUL, config (including secret bytes), six profile-local Desktop
metadata/room registries, six cron manifests, and six entire skill trees are
bound, along with the separately mounted shared fleet-skills tree. Every
profile must load only /opt/data/fleet-skills as an external skill directory;
this is the launcher mount contract. This reads config bytes to hash them,
NEVER prints them or any digest.
If installed Hermes config/skills are symbolic links, this predicate rejects
them rather than guessing what a mutable external target means.

The root profile.yaml Desktop ui_meta registry must contain both listed native
rooms: current id:<client_room_id> keys require roomId exactly equal to the
nonempty key suffix; legacy name:<Display Name> keys require roomId absent or
null and a matching name. Both need active revisions (id tombstones are final;
legacy name tombstones are revision-gated) and all six profiles as members with
the operator-attested exact connection id/kind/label and no known source-missing
or source-unreachable flag. A hosted groups.send/engine-only room does not meet
this root ui_meta requirement. A remote connection id must be a Desktop registry
slug the operator read from their own client (connections.json id / host.agents
source). It is minted from the connection LABEL, survives renames, and is never
derived from a gateway hostname, URL, or tailnet name; a guessed id seats ghost
members that Desktop's gateway filter hides and turns cannot reach.
The coordination room's saved log must contain the witnessed user post and
named member-profile reply with exact ids/text, source, common thread, and
ordered timestamps inside the bound formation cycle.
That on-disk log is *not* proof of rendering. The witness is the separate,
explicit human claim of physically seeing both rooms and the reply in Desktop.
The operator/auditor names and timestamps are claims, not signatures. Their
identities cannot be proved from uid and mode. Agent-authored evidence is NOT
self-certification; the human operator and distinct auditor own acceptance.

The bound cycle evidence is a JSON object with schema=1, instance, map_version,
started_at, completed_at, roles (role->profile), and rooms (same as receipt).
Each role artifact is a nonempty, distinct regular file in hermes-data. The
cycle and role evidence are agent-authored and can be false; the independent
human audits them. Client evidence is a private 0600 host-owned PNG/JPEG
screenshot and audit evidence is a private 0600 host-owned nonempty file, both
under formation-evidence/ (outside the hermes-data bind mount). Their hashes
bind content, not provenance or image authenticity. Witness/cycle/audit and
operator times must be ordered within 24 hours of acceptance, which cannot be
future. No receipt expiration is inferred from disk alone: reconfirm live
conditions separately when needed. This command never prints evidence/ref
contents, secret-bearing paths, identities, or digests; denial uses codes.
"""
from __future__ import annotations

# Reading arbitrary untrusted YAML can be resource-intensive even with SafeLoader;
# restrict bounded profile metadata and never emit its contents.
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

import yaml

RECEIPT = "formation-admission.json"
SCOPE = "mission-buildout"
STAMP = timedelta(hours=24)
HEX = re.compile(r"[0-9a-f]{64}\Z")
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MAP_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
# A Desktop connection id is a registry slug minted from the connection LABEL
# the operator typed (lowercase, non-alphanumeric collapsed to "-", <=48 chars,
# "-2"/"-3" on collision). It is NOT derivable from a gateway hostname, URL, or
# tailnet name, and a later rename keeps the original id. Only the operator can
# read it from the Desktop registry, so the exact value is attested here and
# matched against every saved room seat, never reconstructed from the host.
CONNECTION_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
CHUNK = 128 * 1024
MAX_RECEIPT = 128 * 1024
MAX_METADATA = 4 * 1024 * 1024
MAX_ARTIFACT = 128 * 1024 * 1024
MAX_TREE_FILES = 20000
# Native/curator telemetry is mutable even when skill instructions are not.
# Ignore only these known sidecars at a profile skills/ root, never nested
# instruction or executable files. This is a capability-corpus hash, not a
# snapshot of skill usage or curator state; those require separate review.
SKILL_ROOT_TELEMETRY = frozenset({".usage.json", ".usage.json.lock", ".locks",
                                  ".curator_state", ".curator_ledger.jsonl"})


class Denied(Exception):
    """No trusted accepted snapshot; message is a fixed code, never raw data."""


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise Denied(code)


def _keys(value: object, names: set[str], code: str) -> dict:
    _need(isinstance(value, dict) and set(value) == names, code)
    return value


def _text(value: object, code: str, minimum: int = 1) -> str:
    _need(isinstance(value, str) and len(value.strip()) >= minimum and len(value) <= 10000, code)
    return value


def _time(value: object, code: str) -> datetime:
    _need(isinstance(value, str) and value.endswith("Z"), code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise Denied(code) from None
    _need(parsed.utcoffset() == timedelta(0), code)
    return parsed


def _json(data: bytes, code: str) -> object:
    def unique(fields: list[tuple[str, object]]) -> dict:
        if len(fields) != len(dict(fields)):
            raise ValueError("duplicate")
        return dict(fields)
    try:
        # JSON NaN/Infinity are not valid admission claims.
        def reject_constant(_: str) -> None:
            raise ValueError("nonfinite")
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=reject_constant)
    except (ValueError, UnicodeError, TypeError):
        raise Denied(code) from None


def _parts(path: object) -> list[str]:
    _need(isinstance(path, str) and 0 < len(path) <= 1024 and not path.startswith("/"),
          "unsafe_reference")
    parts = path.split("/")
    _need(all(part not in ("", ".", "..") and "\\" not in part and "\x00" not in part
              and all(ord(c) >= 32 for c in part) for part in parts), "unsafe_reference")
    return parts


def _open_dir(parent: int, name: str, code: str) -> int:
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                       dir_fd=parent)
    except OSError:
        raise Denied(code) from None


def _instance_fd(instance: Path) -> int:
    raw = os.fspath(instance)
    _need(bool(raw) and ".." not in raw.split("/") and "\x00" not in raw and
          instance.name not in ("", "hermes-data"), "unsafe_instance")
    try:
        fd = os.open("/" if instance.is_absolute() else ".",
                     os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise Denied("unsafe_instance") from None
    try:
        for part in raw.split("/"):
            if part in ("", "."):
                continue
            nxt = _open_dir(fd, part, "unsafe_instance")
            os.close(fd)
            fd = nxt
        meta = os.fstat(fd)
        _need(meta.st_uid == os.geteuid() and not (meta.st_mode & 0o077)
              and stat.S_ISDIR(meta.st_mode), "unsafe_instance")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(root: int, path: str, code: str, *, limit: int = MAX_ARTIFACT,
          private: bool = False, keep: bool = False) -> tuple[str, bytes | None]:
    parts = _parts(path)
    directory = os.dup(root)
    try:
        for part in parts[:-1]:
            nxt = _open_dir(directory, part, "unsafe_reference")
            if private:
                meta = os.fstat(nxt)
                _need(meta.st_uid == os.geteuid() and not meta.st_mode & 0o077,
                      "unsafe_reference")
            os.close(directory)
            directory = nxt
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, dir_fd=directory)
        except OSError:
            raise Denied(code) from None
        try:
            before = os.fstat(fd)
            _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                  and before.st_size <= limit, code)
            if private:
                _need(before.st_uid == os.geteuid() and stat.S_IMODE(before.st_mode) == 0o600,
                      code)
            digest = hashlib.sha256()
            chunks = [] if keep else None
            total = 0
            while True:
                block = os.read(fd, CHUNK)
                if not block:
                    break
                total += len(block)
                _need(total <= limit, code)
                digest.update(block)
                if chunks is not None:
                    chunks.append(block)
            after = os.fstat(fd)
            current = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            def fingerprint(meta: os.stat_result) -> tuple:
                return (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns,
                        meta.st_ctime_ns, meta.st_mode, meta.st_uid, meta.st_nlink)
            _need(total == before.st_size and fingerprint(before) == fingerprint(after)
                  and fingerprint(before) == fingerprint(current), code)
            return digest.hexdigest(), b"".join(chunks) if chunks is not None else None
        finally:
            os.close(fd)
    except OSError:
        raise Denied(code) from None
    finally:
        os.close(directory)


def _tree_hash(root: int, path: str, *, skill_corpus: bool = False) -> str:
    parts = _parts(path)
    directory = os.dup(root)
    try:
        for part in parts:
            nxt = _open_dir(directory, part, "unsafe_reference")
            os.close(directory)
            directory = nxt
        digest = hashlib.sha256(b"formation-admission-tree-v1\0")
        count = [0]

        def walk(fd: int, prefix: str) -> None:
            try:
                names = sorted(os.listdir(fd))
            except OSError:
                raise Denied("unsafe_reference") from None
            for name in names:
                relative = prefix + name
                count[0] += 1
                _need(count[0] <= MAX_TREE_FILES, "unsafe_reference")
                # Paths are length-prefixed, avoiding ambiguity even with odd names.
                payload = os.fsencode(relative)
                _need(b"\x00" not in payload and len(payload) <= 4096,
                      "unsafe_reference")
                try:
                    meta = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except OSError:
                    raise Denied("unsafe_reference") from None
                if skill_corpus and not prefix and name in SKILL_ROOT_TELEMETRY:
                    _need(stat.S_ISDIR(meta.st_mode) if name == ".locks" else
                          stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1,
                          "unsafe_reference")
                    continue
                if stat.S_ISDIR(meta.st_mode):
                    digest.update(b"D" + len(payload).to_bytes(4, "big") + payload)
                    child = _open_dir(fd, name, "unsafe_reference")
                    try:
                        walk(child, relative + "/")
                    finally:
                        os.close(child)
                else:
                    _need(stat.S_ISREG(meta.st_mode), "unsafe_reference")
                    hexdigest, _ = _read(fd, name, "unsafe_reference")
                    digest.update(b"F" + len(payload).to_bytes(4, "big") + payload
                                  + bytes.fromhex(hexdigest))
        walk(directory, "")
        return digest.hexdigest()
    finally:
        os.close(directory)


def _ref(root: int, value: object, *, expected: str | None = None,
         prefix: str | None = None, tree: bool = False, private: bool = False,
         keep: bool = False, require_nonempty: bool = False,
         limit: int = MAX_ARTIFACT) -> bytes | None:
    data = _keys(value, {"path", "sha256"}, "invalid_reference")
    path = data["path"]
    _parts(path)
    _need(expected is None or path == expected, "invalid_reference")
    _need(prefix is None or
          (path.startswith(prefix) and len(path) > len(prefix)), "invalid_reference")
    _need(isinstance(data["sha256"], str) and HEX.fullmatch(data["sha256"]) is not None,
          "invalid_reference")
    if tree:
        digest, content = _tree_hash(root, path,
                                     skill_corpus=path.endswith("/skills")), None
    else:
        digest, content = _read(root, path, "unsafe_reference", private=private,
                                limit=limit, keep=keep)
        if require_nonempty:
            # _read's digest is SHA256 even when keep=False. Empty evidence
            # must fail without retaining or printing arbitrary private data.
            _need(digest != hashlib.sha256(b"").hexdigest(), "invalid_evidence")
        _need(content is None or bool(content), "invalid_evidence")
    _need(digest == data["sha256"], "stale_binding")
    return content


class _StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        self.flatten_mapping(node)
        result: dict = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, (str, int, float, bool)) or key in result:
                raise ValueError("duplicate/invalid yaml key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _registry(data: bytes, roles: dict[str, str], rooms: dict, witness: dict,
              connection: dict, started: datetime, completed: datetime) -> None:
    try:
        root = yaml.load(data, Loader=_StrictLoader)
    except (yaml.YAMLError, ValueError, UnicodeError):
        raise Denied("invalid_room_registry") from None
    _need(isinstance(root, dict), "invalid_room_registry")
    ui = root.get("ui_meta")
    groups = ui.get("hermes-bots-groups") if isinstance(ui, dict) else None
    saved = groups.get("rooms") if isinstance(groups, dict) else None
    _need(isinstance(saved, dict), "invalid_room_registry")
    deleted = groups.get("deleted", {})
    _need(isinstance(deleted, dict), "invalid_room_registry")
    for room_key in rooms.values():
        _need(isinstance(room_key, str) and room_key in saved, "invalid_room_registry")
        room = saved[room_key]
        revision = room.get("revision") if isinstance(room, dict) else None
        _need(type(revision) is int and revision >= 0, "invalid_room_registry")
        if room_key.startswith("id:"):
            # Current Desktop mints a client roomId and keys the saved projection
            # by that exact id. Its tombstone is final, even if a stale saved
            # room has a higher revision. A hosted engine-only room is never
            # found in this root ui_meta registry.
            _need(len(room_key) > 3 and room_key not in deleted
                  and room.get("roomId") == room_key[3:]
                  and isinstance(room.get("name"), str) and bool(room["name"].strip()),
                  "invalid_room_registry")
        else:
            # Legacy Desktop rooms omit roomId or use null. Only these name:
            # keys use revision-gated tombstones.
            tombstone = deleted.get(room_key)
            _need(room_key.startswith("name:") and len(room_key) > 5
                  and (tombstone is None or type(tombstone) is int and tombstone < revision)
                  and room.get("roomId") is None and room.get("name") == room_key[5:],
                  "invalid_room_registry")
        _need(isinstance(room.get("members"), list), "invalid_room_registry")
        members = room["members"]
        _need(len(members) == 6 and
              {m.get("name") for m in members if isinstance(m, dict)} == set(roles.values())
              and all(isinstance(m, dict) and m.get("connectionId") == connection["id"]
                      and m.get("connectionKind") == connection["kind"]
                      and m.get("connectionLabel") == connection["label"]
                      and m.get("sourceScoped") is True
                      and m.get("sourceMissing") is not True
                      and m.get("sourceReachable") is not False for m in members),
              "invalid_room_registry")
    log = saved[rooms["coordination"]].get("log")
    _need(isinstance(log, list), "invalid_witness")
    witnessed = []
    for field_id, field_text, sender_kind, sender_name in (
            ("post_id", "seen_post_text", "user", None),
            ("reply_id", "seen_reply_text", "member", witness["reply_profile"])):
        matched = [event for event in log if isinstance(event, dict)
                   and event.get("id") == witness[field_id]
                   and event.get("text") == witness[field_text]
                   and isinstance(event.get("from"), dict)
                   and event["from"].get("kind") == sender_kind
                   and (sender_name is None or event["from"].get("name") == sender_name)
                   and (sender_kind != "member" or connection["kind"] == "local"
                        or event["from"].get("source") == connection["label"])]
        _need(len(matched) == 1, "invalid_witness")
        witnessed.append(matched[0])
    post, reply = witnessed
    _need(isinstance(post.get("thread"), str) and bool(post["thread"])
          and post["thread"] == reply.get("thread")
          and type(post.get("at")) is int and type(reply.get("at")) is int
          and int(started.timestamp() * 1000) <= post["at"] < reply["at"] <=
          int(completed.timestamp() * 1000), "invalid_witness")


def _roster(root: int, roles: dict[str, str]) -> None:
    _need("default" in roles.values(), "invalid_roster")
    home = _open_dir(root, "hermes-data", "invalid_roster")
    try:
        profile_dir = _open_dir(home, "profiles", "invalid_roster")
        try:
            try:
                names = os.listdir(profile_dir)
            except OSError:
                raise Denied("invalid_roster") from None
            native: list[str] = []
            for name in names:
                if name.startswith("."):
                    continue
                _need(IDENT.fullmatch(name) is not None and name != "default", "invalid_roster")
                child = _open_dir(profile_dir, name, "invalid_roster")
                os.close(child)
                native.append(name)
            _need(len(native) == 5 and set(native) == set(roles.values()) - {"default"},
                  "invalid_roster")
        finally:
            os.close(profile_dir)
    finally:
        os.close(home)


def _accepted(root: int, instance: str, scope: str, now: datetime) -> None:
    _, blob = _read(root, RECEIPT, "unsafe_receipt", limit=MAX_RECEIPT,
                    private=True, keep=True)
    receipt = _keys(_json(blob, "invalid_receipt"),
                    {"schema", "instance", "mode", "scope", "accepted_at", "map_version",
                     "roles", "rooms", "member_connection", "operator_declaration",
                     "human_client_witness", "independent_auditor", "bindings", "evidence"},
                    "invalid_receipt")
    _need(type(receipt["schema"]) is int and receipt["schema"] == 1
          and receipt["instance"] == instance and receipt["mode"] == "crew"
          and receipt["scope"] == scope == SCOPE, "invalid_scope_or_instance")
    accepted = _time(receipt["accepted_at"], "invalid_time")
    _need(accepted <= now, "invalid_time")
    version = _text(receipt["map_version"], "invalid_map")
    _need(MAP_VERSION.fullmatch(version) is not None and not version.endswith("."), "invalid_map")
    listed = receipt["roles"]
    _need(isinstance(listed, list) and len(listed) == 6, "invalid_roster")
    roles: dict[str, str] = {}
    for item in listed:
        item = _keys(item, {"role", "profile"}, "invalid_roster")
        role, profile = item["role"], item["profile"]
        _need(isinstance(role, str) and IDENT.fullmatch(role) is not None
              and isinstance(profile, str) and IDENT.fullmatch(profile) is not None
              and role not in roles, "invalid_roster")
        roles[role] = profile
    _need(len(set(roles.values())) == 6, "invalid_roster")
    _roster(root, roles)
    rooms = _keys(receipt["rooms"], {"coordination", "retrospective"}, "invalid_room_registry")
    _need(all(isinstance(v, str) and
              ((v.startswith("name:") and len(v) > 5) or
               (v.startswith("id:") and bool(v[3:].strip())))
              for v in rooms.values()) and len(set(rooms.values())) == 2,
          "invalid_room_registry")
    connection = _keys(receipt["member_connection"],
                       {"id", "kind", "label"}, "invalid_connection")
    connection_id = _text(connection["id"], "invalid_connection")
    _text(connection["label"], "invalid_connection")
    _need(connection["kind"] in ("remote", "local") and
          ((connection_id == "local" and connection["kind"] == "local") or
           (connection["kind"] == "remote" and connection_id != "local"
            and len(connection_id) <= 48
            and CONNECTION_ID.fullmatch(connection_id) is not None)),
          "invalid_connection")
    operator = _keys(receipt["operator_declaration"],
                     {"name", "signed_at", "statement"}, "invalid_operator")
    operator_name = _text(operator["name"], "invalid_operator")
    _need(operator["statement"] == "I accept the bound operating map and this crew for mission-buildout only",
          "invalid_operator")
    signed = _time(operator["signed_at"], "invalid_operator")
    witness = _keys(receipt["human_client_witness"],
                    {"observer", "observed_at", "client", "physically_seen",
                     "room_keys", "post_id", "reply_id", "reply_profile",
                     "seen_post_text", "seen_reply_text"}, "invalid_witness")
    witness_name = _text(witness["observer"], "invalid_witness")
    observed = _time(witness["observed_at"], "invalid_witness")
    _need(witness["client"] == "Hermes Desktop" and witness["physically_seen"] is True
          and witness["room_keys"] == [rooms["coordination"], rooms["retrospective"]]
          and witness["reply_profile"] in roles.values()
          and witness["post_id"] != witness["reply_id"], "invalid_witness")
    for key in ("post_id", "reply_id"):
        _text(witness[key], "invalid_witness")
    for key in ("seen_post_text", "seen_reply_text"):
        _text(witness[key], "invalid_witness", minimum=12)
    auditor = _keys(receipt["independent_auditor"],
                    {"name", "reviewed_at", "finding", "observations"}, "invalid_auditor")
    audit_name = _text(auditor["name"], "invalid_auditor")
    reviewed = _time(auditor["reviewed_at"], "invalid_auditor")
    _need(audit_name.casefold().strip() not in {operator_name.casefold().strip(),
           witness_name.casefold().strip(), *(n.casefold() for n in roles.values())}
          and auditor["finding"] == "pass", "invalid_auditor")
    _text(auditor["observations"], "invalid_auditor", minimum=32)
    bindings: dict = _keys(receipt["bindings"],
                           {"control_env", "operating_map", "fleet_skills"} |
                           {f"{p}.{kind}" for p in roles.values()
                            for kind in ("soul", "config", "profile", "cron", "skills")},
                           "invalid_binding")
    control_blob = _ref(root, bindings["control_env"], expected="control.env",
                        keep=True, limit=MAX_METADATA)
    try:
        hostnames = [line.split("=", 1)[1].strip().strip('"').strip("'")
                     for line in control_blob.decode("utf-8").splitlines()
                     if line.startswith("TAILSCALE_HOSTNAME=")]
    except UnicodeError:
        raise Denied("invalid_connection") from None
    # The control.env hostname binds the instance, NOT the Desktop connection
    # id: the id comes from the operator's client registry and frequently does
    # not contain the hostname at all. Requiring a hostname prefix here
    # rejected legitimate registries and invited hostname-derived guesses.
    _need(len(hostnames) == 1 and IDENT.fullmatch(hostnames[0]) is not None,
          "invalid_connection")
    _ref(root, bindings["fleet_skills"], expected="hermes-data/fleet-skills", tree=True)
    map_ref = bindings["operating_map"]
    _need(isinstance(map_ref, dict) and map_ref.get("path") != RECEIPT,
          "invalid_binding")
    map_path = map_ref.get("path")
    map_blob = _ref(root, map_ref, prefix="hermes-data/", keep=True, limit=MAX_METADATA)
    if map_path.endswith("/FLEET_OPERATING_MAP.md"):
        # Actual fleet operating maps are versioned Markdown, not synthetic
        # JSON files. Match a standalone Version label near the top and do
        # not mistake an unversioned document elsewhere for the bound map.
        try:
            opening = map_blob.decode("utf-8").splitlines()[:16]
        except UnicodeError:
            raise Denied("invalid_map") from None
        labels = [line.removeprefix("Version:").strip().split(maxsplit=1)[0].rstrip(".")
                  for line in opening if line.startswith("Version:") and
                  line.removeprefix("Version:").strip()]
        _need(labels == [version], "invalid_map")
    elif isinstance(map_path, str) and map_path.endswith(".json"):
        operating_map = _json(map_blob, "invalid_map")
        _need(isinstance(operating_map, dict) and
              operating_map.get("map_version") == version, "invalid_map")
    else:
        raise Denied("invalid_map")
    profile_data = None
    for p in sorted(roles.values()):
        prefix = "hermes-data" if p == "default" else f"hermes-data/profiles/{p}"
        for kind, suffix in (("soul", "SOUL.md"), ("config", "config.yaml"),
                             ("profile", "profile.yaml"), ("cron", "cron/jobs.json"),
                             ("skills", "skills")):
            content = _ref(root, bindings[f"{p}.{kind}"], expected=f"{prefix}/{suffix}",
                           tree=(kind == "skills"), keep=(p == "default" and kind == "profile"),
                           limit=MAX_METADATA if kind == "profile" else MAX_ARTIFACT)
            if kind == "config":
                # These installed profiles load the separately mounted shared
                # policy tree. A different/unbound external skill directory
                # changes effective instructions, so fail closed.
                _, raw_config = _read(root, f"{prefix}/config.yaml", "invalid_binding",
                                      keep=True, limit=MAX_METADATA)
                try:
                    parsed_config = yaml.load(raw_config, Loader=_StrictLoader)
                except (yaml.YAMLError, ValueError, UnicodeError):
                    raise Denied("invalid_binding") from None
                skills_config = parsed_config.get("skills") if isinstance(parsed_config, dict) else None
                _need(isinstance(skills_config, dict) and
                      skills_config.get("external_dirs") == ["/opt/data/fleet-skills"],
                      "unbound_skill_source")
            if p == "default" and kind == "profile":
                profile_data = content
    evidence = _keys(receipt["evidence"], {"cycle", "client", "audit"} |
                     {f"role.{role}" for role in roles}, "invalid_evidence")
    all_paths = [ref.get("path") if isinstance(ref, dict) else None for ref in
                 list(bindings.values()) + list(evidence.values())]
    _need(len(set(map(str, all_paths))) == len(all_paths), "invalid_reference")
    cycle_blob = _ref(root, evidence["cycle"], prefix="hermes-data/", keep=True,
                      limit=MAX_METADATA)
    cycle = _keys(_json(cycle_blob, "invalid_evidence"),
                  {"schema", "instance", "map_version", "started_at", "completed_at",
                   "roles", "rooms"}, "invalid_evidence")
    started = _time(cycle["started_at"], "invalid_evidence")
    completed = _time(cycle["completed_at"], "invalid_evidence")
    _need(type(cycle["schema"]) is int and cycle["schema"] == 1
          and cycle["instance"] == instance and cycle["map_version"] == version
          and cycle["roles"] == roles and cycle["rooms"] == rooms,
          "invalid_evidence")
    _need(started <= completed <= observed <= reviewed <= signed <= accepted
          and accepted - started <= STAMP, "invalid_time")
    _registry(profile_data, roles, rooms, witness, connection, started, completed)
    for role in roles:
        _ref(root, evidence[f"role.{role}"], prefix="hermes-data/", require_nonempty=True)
    image = _ref(root, evidence["client"], prefix="formation-evidence/",
                 private=True, keep=True, limit=MAX_METADATA)
    _need(image.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))
          and len(image) >= 32, "invalid_witness")
    _ref(root, evidence["audit"], prefix="formation-evidence/", private=True,
         require_nonempty=True)


def inspect(instance_dir: Path, scope: str = SCOPE, *,
            now: datetime | None = None) -> tuple[dict, int]:
    """No side effects; return a data-minimal denial code, not evidence contents."""
    report = {"schema": 1, "instance": Path(instance_dir).name, "scope": scope,
              "read_only": True, "status": "denied", "admitted": False,
              "operator_attested": False, "native_enforced": False,
              "live_client_verified": False}
    fd = None
    try:
        _need(scope == SCOPE, "invalid_scope_or_instance")
        _need(isinstance(now, datetime) or now is None, "invalid_time")
        fd = _instance_fd(Path(instance_dir))
        _accepted(fd, Path(instance_dir).name, scope, now or datetime.now(timezone.utc))
        report["status"] = "accepted_operator_attested"
        report["admitted"] = True
        report["operator_attested"] = True
        return report, 0
    except Denied as exc:
        report["reason"] = str(exc)
        return report, 1
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        # Unexpected malformed input must fail closed without disclosing paths.
        report["reason"] = "invalid_or_unreadable"
        return report, 1
    finally:
        if fd is not None:
            os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-dir", type=Path, required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--json", action="store_true", help="output one JSON decision")
    args = parser.parse_args(argv)
    report, rc = inspect(args.instance_dir, args.scope)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"{report['status']}: operator-attested snapshot, not native enforced"
              if rc == 0 else f"denied: {report['reason']}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
