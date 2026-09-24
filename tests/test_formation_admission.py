"""Synthetic, disk-only tests of operator-attested formation admission."""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

import yaml
from lib import formation_admission as fa


T0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
ROLES = {"founder": "default", "scout": "scout", "researcher": "researcher",
         "curator": "curator", "auditor": "auditor", "operator": "operator"}
ROOMS = {"coordination": "name:Coordination", "retrospective": "name:Retro"}


def stamp(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


class FormationAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.instance = self.parent / "synthetic"
        self.instance.mkdir(mode=0o700)
        self.home = self.instance / "hermes-data"
        self.home.mkdir()
        self.evidence_dir = self.instance / "formation-evidence"
        self.evidence_dir.mkdir(mode=0o700)
        self.receipt = self.instance / fa.RECEIPT
        self.paths = []
        self.put("control.env", "INSTANCE=synthetic\nTAILSCALE_HOSTNAME=synthetic\n")
        self.put("hermes-data/map.json", json.dumps({"map_version": "v0", "roster": ROLES}))
        self.put("hermes-data/fleet-skills/formation/SKILL.md", "# shared fleet formation policy\n")
        log = [{"id": "post-17", "text": "Coordination cycle post visible to operator",
                "from": {"kind": "user", "name": "Operator Human"}, "thread": "cycle-1",
                "at": int((T0 + timedelta(minutes=3)).timestamp() * 1000)},
               {"id": "reply-18", "text": "Reply physically visible to operator",
                "from": {"kind": "member", "name": "scout", "source": "synthetic"},
                "thread": "cycle-1", "at": int((T0 + timedelta(minutes=4)).timestamp() * 1000)}]
        # Native name: Desktop rooms normally omit roomId (explicit null is
        # also valid). Avoid treating a missing engine binding as absent room.
        rooms = {name: {"name": name[5:], "revision": 3,
                        "members": [{"name": p, "connectionId": "synthetic-tail123-ts-net",
                                     "connectionKind": "remote", "connectionLabel": "synthetic",
                                     "sourceScoped": True} for p in ROLES.values()],
                        "log": log if kind == "coordination" else []}
                 for kind, name in ROOMS.items()}
        self.registry = {"ui_meta": {"hermes-bots-groups": {"rooms": rooms}}}
        self.bindings = {"control_env": self.ref("control.env"),
                         "operating_map": self.ref("hermes-data/map.json"),
                         "fleet_skills": self.ref("hermes-data/fleet-skills", tree=True)}
        for profile in ROLES.values():
            base = "hermes-data" if profile == "default" else f"hermes-data/profiles/{profile}"
            self.put(f"{base}/SOUL.md", f"# {profile} SOUL\nOwn role: {profile}\n")
            self.put(f"{base}/config.yaml", f"key: private-{profile}-secret\nskills:\n  external_dirs:\n    - /opt/data/fleet-skills\n")
            self.put(f"{base}/profile.yaml", yaml.safe_dump(self.registry if profile == "default" else
                                                       {"name": profile, "ui_meta": {}}, sort_keys=False))
            self.put(f"{base}/cron/jobs.json", '{"jobs": []}')
            self.put(f"{base}/skills/formation/SKILL.md", f"# skill for {profile}\n")
            self.bindings.update({
                f"{profile}.soul": self.ref(f"{base}/SOUL.md"),
                f"{profile}.config": self.ref(f"{base}/config.yaml"),
                f"{profile}.profile": self.ref(f"{base}/profile.yaml"),
                f"{profile}.cron": self.ref(f"{base}/cron/jobs.json"),
                f"{profile}.skills": self.ref(f"{base}/skills", tree=True),
            })
        cycle = {"schema": 1, "instance": "synthetic", "map_version": "v0",
                 "started_at": stamp(0), "completed_at": stamp(6),
                 "roles": ROLES, "rooms": ROOMS}
        self.put("hermes-data/formation/cycle.json", json.dumps(cycle))
        self.evidence = {"cycle": self.ref("hermes-data/formation/cycle.json")}
        for role in ROLES:
            path = f"hermes-data/formation/{role}.txt"
            self.put(path, f"{role}: real bounded independent role step, reviewed externally\n")
            self.evidence[f"role.{role}"] = self.ref(path)
        self.put("formation-evidence/client.png", b"\x89PNG\r\n\x1a\n" + b"synthetic pixel" * 4, private=True)
        self.put("formation-evidence/audit.txt", "Independent inspection for all six roles and Desktop view", private=True)
        self.evidence["client"] = self.ref("formation-evidence/client.png")
        self.evidence["audit"] = self.ref("formation-evidence/audit.txt")
        self.data = {
            "schema": 1, "instance": "synthetic", "mode": "crew",
            "scope": "mission-buildout", "accepted_at": stamp(12), "map_version": "v0",
            "roles": [{"role": role, "profile": profile} for role, profile in ROLES.items()],
            "rooms": ROOMS,
            "member_connection": {"id": "synthetic-tail123-ts-net", "kind": "remote", "label": "synthetic"},
            "operator_declaration": {"name": "Operator Human", "signed_at": stamp(11),
                                     "statement": "I accept the bound operating map and this crew for mission-buildout only"},
            "human_client_witness": {"observer": "Operator Human", "observed_at": stamp(7),
                "client": "Hermes Desktop", "physically_seen": True,
                "room_keys": list(ROOMS.values()), "post_id": "post-17", "reply_id": "reply-18",
                "reply_profile": "scout",
                "seen_post_text": "Coordination cycle post visible to operator",
                "seen_reply_text": "Reply physically visible to operator"},
            "independent_auditor": {"name": "Independent Human", "reviewed_at": stamp(10),
                                    "finding": "pass",
                                    "observations": "I inspected each distinct role artifact and the client screenshot."},
            "bindings": self.bindings, "evidence": self.evidence,
        }
        self.publish()
        self.valid_data = copy.deepcopy(self.data)

    def put(self, relative, content, *, private=False):
        path = self.instance / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        if private:
            path.chmod(0o600)
        self.paths.append(path)
        return path

    def ref(self, relative, *, tree=False):
        path = self.instance / relative
        if tree:
            fd = os.open(self.instance, os.O_RDONLY | os.O_DIRECTORY)
            try:
                hash_ = fa._tree_hash(fd, relative,
                                     skill_corpus=relative.endswith("/skills"))
            finally:
                os.close(fd)
        else:
            hash_ = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"path": relative, "sha256": hash_}

    def publish(self):
        self.receipt.write_text(json.dumps(self.data), encoding="utf-8")
        self.receipt.chmod(0o600)

    def decision(self, want=1, *, now=T0 + timedelta(hours=1)):
        result, rc = fa.inspect(self.instance, "mission-buildout", now=now)
        self.assertEqual(rc, want, result)
        self.assertEqual(result["read_only"], True)
        self.assertFalse(result["native_enforced"])
        self.assertFalse(result["live_client_verified"])
        self.assertEqual(result["admitted"], rc == 0)
        return result

    def test_synthetic_yes_cli_and_read_only_mtime(self):
        watched = [self.receipt] + self.paths
        before = [(path, path.read_bytes(), path.stat().st_mtime_ns) for path in watched]
        contents = set(self.instance.rglob("*"))
        report = self.decision(0)
        self.assertTrue(report["operator_attested"])
        self.assertEqual(report["status"], "accepted_operator_attested")
        cli = [sys.executable, "-B", str(Path(fa.__file__)), "--instance-dir", str(self.instance),
               "--scope", "mission-buildout", "--json"]
        # CLI uses real time. Only adjust the synthetic receipt/timestamps for it.
        now = datetime.now(timezone.utc)
        # Keep accepted_at safely in the past despite subprocess startup time.
        delta = now - T0 - timedelta(minutes=20)
        for field in ("accepted_at",):
            self.data[field] = (datetime.fromisoformat(self.data[field].replace("Z", "+00:00")) +
                                delta).isoformat().replace("+00:00", "Z")
        for key, field in (("operator_declaration", "signed_at"),
                           ("human_client_witness", "observed_at"),
                           ("independent_auditor", "reviewed_at")):
            self.data[key][field] = (datetime.fromisoformat(self.data[key][field].replace("Z", "+00:00")) +
                                     delta).isoformat().replace("+00:00", "Z")
        cycle = self.instance / self.evidence["cycle"]["path"]
        cycle_data = json.loads(cycle.read_text())
        for field in ("started_at", "completed_at"):
            cycle_data[field] = (datetime.fromisoformat(cycle_data[field].replace("Z", "+00:00")) +
                                 delta).isoformat().replace("+00:00", "Z")
        cycle.write_text(json.dumps(cycle_data))
        self.data["evidence"]["cycle"] = self.ref(self.evidence["cycle"]["path"])
        root_profile = self.home / "profile.yaml"
        native_registry = yaml.safe_load(root_profile.read_text())
        for event in native_registry["ui_meta"]["hermes-bots-groups"]["rooms"][ROOMS["coordination"]]["log"]:
            event["at"] += int(delta.total_seconds() * 1000)
        root_profile.write_text(yaml.safe_dump(native_registry, sort_keys=False))
        self.data["bindings"]["default.profile"] = self.ref("hermes-data/profile.yaml")
        self.publish()
        before = [(path, path.read_bytes(), path.stat().st_mtime_ns) for path in watched]
        completed = subprocess.run(cli, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, (completed.stdout, completed.stderr))
        output = json.loads(completed.stdout)
        self.assertTrue(output["admitted"])
        self.assertFalse(output["native_enforced"])
        self.assertNotIn("secret", completed.stdout.lower())
        self.assertNotIn("sha256", completed.stdout.lower())
        self.assertEqual([(path, path.read_bytes(), path.stat().st_mtime_ns) for path in watched], before)
        self.assertEqual(set(self.instance.rglob("*")), contents)

    def test_absent_wrong_owner_group_write_symlink_hardlink_and_private_dir(self):
        self.decision(0)
        self.receipt.unlink()
        before = [(path, path.stat().st_mtime_ns) for path in self.paths]
        self.assertEqual(self.decision()["reason"], "unsafe_receipt")
        self.assertEqual([(path, path.stat().st_mtime_ns) for path in self.paths], before)
        self.assertFalse(self.receipt.exists())
        self.publish()
        for mode in (0o644, 0o660, 0o400, 0o666):
            with self.subTest(mode=mode):
                self.receipt.chmod(mode)
                self.assertEqual(self.decision()["reason"], "unsafe_receipt")
        self.receipt.chmod(0o600)
        other = self.instance / "duplicate"
        os.link(self.receipt, other)
        self.decision()
        other.unlink()
        target = self.receipt.read_bytes()
        self.receipt.unlink()
        other.write_bytes(target)
        self.receipt.symlink_to(other)
        self.decision()
        self.receipt.unlink()
        other.unlink()
        self.publish()
        self.instance.chmod(0o770)
        self.assertEqual(self.decision()["reason"], "unsafe_instance")
        self.instance.chmod(0o700)
        if os.geteuid() == 0:
            os.chown(self.receipt, 65534, -1)
            self.decision()
            os.chown(self.receipt, os.geteuid(), -1)
        else:
            # Same unsafe-owner predicate also denies tampered uid metadata.
            self.assertNotEqual(os.geteuid(), -1)

    def test_invalid_scope_instance_roster_audit_witness_map_and_evidence(self):
        changes = [
            lambda d: d.update(scope="production-release"),
            lambda d: d.update(instance="other"),
            lambda d: d.update(mode="solo"),
            lambda d: d.update(schema=True),
            lambda d: d.update(roles=d["roles"][:-1]),
            lambda d: d["roles"][0].update(profile="scout"),
            lambda d: d["roles"][0].update(role=""),
            lambda d: d["operator_declaration"].update(statement="operator says ok"),
            lambda d: d["independent_auditor"].update(name="Operator Human"),
            lambda d: d["independent_auditor"].update(finding="pending"),
            lambda d: d["human_client_witness"].update(physically_seen=False),
            lambda d: d["human_client_witness"].update(client="backend readback"),
            lambda d: d["human_client_witness"].update(room_keys=["id:engine-room"]),
            lambda d: d["human_client_witness"].update(seen_reply_text="ack"),
            lambda d: d["human_client_witness"].update(reply_id="missing"),
            lambda d: d["human_client_witness"].update(reply_profile="ghost"),
            lambda d: d["bindings"]["operating_map"].update(sha256="0" * 64),
            lambda d: d["evidence"].pop("role.founder"),
            lambda d: d["evidence"]["client"].update(path="hermes-data/formation/founder.txt"),
            lambda d: d["evidence"]["client"].update(path="/tmp/client.png"),
            lambda d: d["evidence"]["client"].update(path="formation-evidence/../../tmp/client.png"),
            lambda d: d.update(accepted_at=stamp(1)),
            lambda d: d["independent_auditor"].update(reviewed_at=stamp(30)),
            lambda d: d["bindings"]["scout.soul"].update(path="hermes-data/SOUL.md"),
            lambda d: d["bindings"]["default.config"].update(path="hermes-data/config.yaml/../secret"),
            lambda d: d["evidence"]["role.scout"].update(path="hermes-data/formation/cycle.json"),
        ]
        for i, change in enumerate(changes):
            with self.subTest(case=i):
                data = copy.deepcopy(self.data)
                change(data)
                self.data = data
                self.publish()
                self.decision()
                self.data = copy.deepcopy(self.valid_data)
                self.publish()

    def test_stale_bindings_unsafe_refs_and_incomplete_six_profile_roster(self):
        self.decision(0)
        for binding in ("control_env", "fleet_skills", "default.soul", "default.config", "default.profile",
                        "default.cron", "default.skills", "scout.soul", "scout.config",
                        "scout.profile", "scout.cron", "scout.skills", "operating_map"):
            with self.subTest(binding=binding):
                rel = self.data["bindings"][binding]["path"]
                target = self.instance / rel
                if target.is_dir():
                    target = target / "formation" / "SKILL.md"
                old = target.read_bytes()
                target.write_bytes(old + b"tamper")
                self.assertEqual(self.decision()["reason"], "stale_binding")
                target.write_bytes(old)
        victim = self.instance / self.data["evidence"]["role.scout"]["path"]
        old = victim.read_bytes()
        victim.write_bytes(old + b"tamper")
        self.decision()
        victim.write_bytes(old)
        victim.unlink()
        victim.symlink_to(self.instance / "control.env")
        self.decision()
        victim.unlink()
        victim.write_bytes(old)
        self.evidence_dir.chmod(0o770)
        self.assertEqual(self.decision()["reason"], "unsafe_reference")
        self.evidence_dir.chmod(0o700)
        profile = self.home / "profiles/scout"
        hidden = self.home / "profiles/.scout-hidden"
        profile.rename(hidden)
        self.assertEqual(self.decision()["reason"], "invalid_roster")
        hidden.rename(profile)
        self.decision(0)
        (self.home / "profiles/extra").mkdir()
        self.decision()

    def test_denies_bad_json_duplicate_keys_symlinked_instance_and_missing_room(self):
        self.receipt.write_text('{"schema": 1,"schema": 1}')
        self.decision()
        self.receipt.write_text('{')
        self.decision()
        self.data = copy.deepcopy(self.valid_data)
        self.publish()
        self.decision(0)
        alias = self.parent / "alias"
        alias.symlink_to(self.instance)
        self.assertEqual(fa.inspect(alias, now=T0 + timedelta(hours=1))[1], 1)
        nested = self.parent / "linked"
        nested.symlink_to(self.parent)
        self.assertEqual(fa.inspect(nested / "synthetic", now=T0 + timedelta(hours=1))[1], 1)
        p = self.home / "profile.yaml"
        old = p.read_bytes()
        p.write_text("ui_meta: {}")
        self.data["bindings"]["default.profile"] = self.ref("hermes-data/profile.yaml")
        self.publish()
        self.assertEqual(self.decision()["reason"], "invalid_room_registry")
        p.write_bytes(old)
        self.data = copy.deepcopy(self.valid_data)
        room = self.registry["ui_meta"]["hermes-bots-groups"]
        room["deleted"] = {ROOMS["coordination"]: 3}
        p.write_text(yaml.safe_dump(self.registry, sort_keys=False))
        self.data["bindings"]["default.profile"] = self.ref("hermes-data/profile.yaml")
        self.publish()
        self.assertEqual(self.decision()["reason"], "invalid_room_registry")

    def test_bound_map_and_witness_reply_are_semantically_checked(self):
        map_file = self.home / "map.json"
        map_file.write_text(json.dumps({"map_version": "v1"}))
        self.data["bindings"]["operating_map"] = self.ref("hermes-data/map.json")
        self.publish()
        self.assertEqual(self.decision()["reason"], "invalid_map")
        map_file.write_text(json.dumps({"map_version": "v0", "roster": ROLES}))
        self.data = copy.deepcopy(self.valid_data)
        p = self.home / "profile.yaml"
        registry = copy.deepcopy(self.registry)
        registry["ui_meta"]["hermes-bots-groups"]["rooms"][ROOMS["coordination"]]["log"][1]["from"] = {
            "kind": "user", "name": "scout"}
        p.write_text(yaml.safe_dump(registry, sort_keys=False))
        self.data["bindings"]["default.profile"] = self.ref("hermes-data/profile.yaml")
        self.publish()
        self.assertEqual(self.decision()["reason"], "invalid_witness")

    def test_unsafe_intermediate_symlinks_and_host_evidence_modes(self):
        self.decision(0)
        evidence_path = self.evidence_dir / "client.png"
        content = evidence_path.read_bytes()
        evidence_path.unlink()
        outside = self.parent / "outside.png"
        outside.write_bytes(content)
        evidence_path.symlink_to(outside)
        self.assertEqual(self.decision()["reason"], "unsafe_reference")
        evidence_path.unlink()
        evidence_path.write_bytes(content)
        evidence_path.chmod(0o644)
        self.decision()
        evidence_path.chmod(0o600)
        original = self.home / "formation"
        relocated = self.home / "formation-staged"
        original.rename(relocated)
        original.symlink_to(relocated, target_is_directory=True)
        self.assertEqual(self.decision()["reason"], "unsafe_reference")
        original.unlink()
        relocated.rename(original)
        self.decision(0)

    def test_known_skill_root_telemetry_does_not_stale_capability_corpus(self):
        skills = self.home / "profiles/scout/skills"
        for name in (".usage.json", ".usage.json.lock", ".curator_state", ".curator_ledger.jsonl"):
            sidecar = skills / name
            sidecar.write_text("mutable telemetry\n")
            self.decision(0)
            sidecar.write_text("next usage event\n")
            self.decision(0)
        (skills / ".locks").mkdir()
        self.decision(0)
        extra = skills / "formation/.hidden-instructions.py"
        extra.write_text("print('added executable')\n")
        self.assertEqual(self.decision()["reason"], "stale_binding")
        extra.unlink()
        sidecar = skills / ".usage.json"
        sidecar.unlink()
        sidecar.symlink_to(self.home / "config.yaml")
        self.assertEqual(self.decision()["reason"], "unsafe_reference")

    def test_remote_identity_and_bounded_thread_are_not_name_only(self):
        native = self.home / "profile.yaml"
        for change in (
                lambda d: d["ui_meta"]["hermes-bots-groups"]["rooms"][ROOMS["coordination"]]["members"][0].update(connectionId="other-tail-ts-net"),
                lambda d: d["ui_meta"]["hermes-bots-groups"]["rooms"][ROOMS["coordination"]]["members"][0].update(sourceReachable=False),
                lambda d: d["ui_meta"]["hermes-bots-groups"]["rooms"][ROOMS["coordination"]]["log"][1].update(thread="different-cycle"),
                lambda d: d["ui_meta"]["hermes-bots-groups"]["rooms"][ROOMS["coordination"]]["log"][1]["from"].update(source="other-fleet"),
                lambda d: d["ui_meta"]["hermes-bots-groups"]["rooms"][ROOMS["coordination"]]["log"][1].update(at=int((T0 + timedelta(minutes=40)).timestamp() * 1000)),
        ):
            with self.subTest(change=change):
                altered = copy.deepcopy(self.registry)
                change(altered)
                native.write_text(yaml.safe_dump(altered, sort_keys=False))
                self.data["bindings"]["default.profile"] = self.ref("hermes-data/profile.yaml")
                self.publish()
                self.decision()
        native.write_text(yaml.safe_dump(self.registry, sort_keys=False))
        self.data = copy.deepcopy(self.valid_data)
        self.publish()
        for changed in ("/other/shared-policy", "/opt/data/fleet-skills/.."):
            with self.subTest(external_dir=changed):
                config = self.home / "profiles/scout/config.yaml"
                config.write_text("skills:\n  external_dirs:\n    - " + changed + "\n")
                self.data["bindings"]["scout.config"] = self.ref("hermes-data/profiles/scout/config.yaml")
                self.publish()
                self.assertEqual(self.decision()["reason"], "unbound_skill_source")

    def test_empty_role_or_audit_artifact_is_not_a_valid_cycle(self):
        for key in ("role.founder", "role.scout", "audit"):
            with self.subTest(key=key):
                target = self.instance / self.data["evidence"][key]["path"]
                original = target.read_bytes()
                target.write_bytes(b"")
                self.data["evidence"][key] = self.ref(self.data["evidence"][key]["path"])
                self.publish()
                self.assertEqual(self.decision()["reason"], "invalid_evidence")
                target.write_bytes(original)
                self.data = copy.deepcopy(self.valid_data)
                self.publish()

    def test_native_markdown_map_unbound_name_rooms_and_shared_skills(self):
        # BTT's real operating map has this prose-header shape. A synthetic
        # JSON-only map check would reject a legitimate native fleet.
        path = "hermes-data/workspace/mission/FLEET_OPERATING_MAP.md"
        map_file = self.put(path, "# Fleet Operating Map\n\nVersion: 0.1.3-draft. Change: formation first.\n")
        self.data["map_version"] = "0.1.3-draft"
        self.data["bindings"]["operating_map"] = self.ref(path)
        cycle = self.home / "formation/cycle.json"
        cycle_data = json.loads(cycle.read_text())
        cycle_data["map_version"] = "0.1.3-draft"
        cycle.write_text(json.dumps(cycle_data))
        self.data["evidence"]["cycle"] = self.ref("hermes-data/formation/cycle.json")
        self.publish()
        self.decision(0)
        # Explicit null and omitted roomId both describe an unbound Desktop
        # name: room. A non-null engine binding is a different surface.
        profile = self.home / "profile.yaml"
        room = self.registry["ui_meta"]["hermes-bots-groups"]["rooms"][ROOMS["coordination"]]
        room["roomId"] = None
        profile.write_text(yaml.safe_dump(self.registry, sort_keys=False))
        self.data["bindings"]["default.profile"] = self.ref("hermes-data/profile.yaml")
        self.publish()
        self.decision(0)
        room["roomId"] = "engine-room"
        profile.write_text(yaml.safe_dump(self.registry, sort_keys=False))
        self.data["bindings"]["default.profile"] = self.ref("hermes-data/profile.yaml")
        self.publish()
        self.assertEqual(self.decision()["reason"], "invalid_room_registry")
        room.pop("roomId")
        profile.write_text(yaml.safe_dump(self.registry, sort_keys=False))
        self.data["bindings"]["default.profile"] = self.ref("hermes-data/profile.yaml")
        map_file.write_text("# Fleet Operating Map\n\nVersion: 0.1.4-draft. Change: drift.\n")
        self.data["bindings"]["operating_map"] = self.ref(path)
        self.publish()
        self.assertEqual(self.decision()["reason"], "invalid_map")
        map_file.write_text("# Fleet Operating Map\n\nVersion: 0.1.3-draft. Change: formation first.\n")
        self.data["bindings"]["operating_map"] = self.ref(path)
        shared = self.home / "fleet-skills/formation/SKILL.md"
        shared.write_text("# altered shared policy\n")
        self.publish()
        self.assertEqual(self.decision()["reason"], "stale_binding")


if __name__ == "__main__":
    unittest.main()
