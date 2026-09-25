"""Formation admission v2 uses native references, not role evidence files."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import formation_admission as fa


T0 = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
ROLES = {"sense": "default", "propose": "proposer", "challenge": "challenger",
         "execute": "executor", "decide": "decider", "steward": "steward"}
FUNCTIONS = {"sense": ["sensing"], "propose": ["proposal"],
             "challenge": ["challenge"], "execute": ["execution"],
             "decide": ["decision"], "steward": ["stewardship"]}


def stamp(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.instance = Path(self.temp.name) / "synthetic"
        self.instance.mkdir(mode=0o700)
        self.home = self.instance / "hermes-data"
        self.home.mkdir()
        self.receipt = self.instance / fa.RECEIPT
        shared = self.home / "fleet-skills"
        for skill in ("fleet-organism-design", "fleet-convergence-learning"):
            path = shared / skill / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"# {skill}\n")
        for board in ("coordination", "improvement"):
            path = self.home / "kanban/boards" / board
            path.mkdir(parents=True)
            (path / "board.json").write_text(json.dumps({"id": board}))

        people = []
        for role, profile in ROLES.items():
            base = self.home if profile == "default" else self.home / "profiles" / profile
            base.mkdir(parents=True, exist_ok=True)
            (base / "SOUL.md").write_text(f"# {role}\n")
            (base / "config.yaml").write_text(
                "skills:\n  external_dirs:\n    - /opt/data/fleet-skills\n")
            (base / "cron").mkdir()
            (base / "cron/jobs.json").write_text('{"jobs": []}')
            people.append({"role": role, "profile": profile,
                           "responsibility": f"Own {role} for the mission",
                           "functions": FUNCTIONS[role],
                           "tools": ["fleet-convergence-learning", "kanban", "group-chat"],
                           "skills": ["fleet-organism-design", "fleet-convergence-learning"],
                           "authority": ["observe", "recommend"]})
        room_keys = ["id:coordination", "id:improvement"]
        members = [{"name": profile, "connectionId": "desktop-real",
                    "connectionKind": "remote", "connectionLabel": "My Gateway"}
                   for profile in ROLES.values()]
        rooms = {key: {"roomId": key[3:], "name": key[3:], "revision": 2,
                       "members": copy.deepcopy(members), "log": []}
                 for key in room_keys}
        (self.home / "profile.yaml").write_text(yaml.safe_dump(
            {"ui_meta": {"hermes-bots-groups": {"rooms": rooms}}}, sort_keys=False))
        for profile in set(ROLES.values()) - {"default"}:
            (self.home / "profiles" / profile / "profile.yaml").write_text("ui_meta: {}\n")

        transitions = [{"from": source, "to": target,
                        "event": f"{source.lower()}-complete",
                        "guard": "configured-and-observed", "owner": "default",
                        "surface": "kanban"}
                       for source, target in fa.NORMAL_EDGES]
        self.contract = {
            "schema": 1, "map_version": "v1", "stage": "FORMATION_PASSED",
            "mission": {"id": "synthetic-mission", "source": "operator:request-1"},
            "personas": people,
            "surfaces": {
                "truth": "authoritative-tools",
                "flow": {"kind": "kanban", "coordination_board": "coordination",
                         "improvement_board": "improvement"},
                "deliberation": {"kind": "group-chat", "coordination_room": room_keys[0],
                                 "improvement_room": room_keys[1]},
                "memory": {"kind": "convergence-ledger",
                           "path": "hermes-data/fleet-state/convergence.db",
                           "recall_limit": 5, "char_budget": 4000},
                "cognition": "ephemeral-session",
                "timing": "native-schedules-and-triggers"},
            "state_machine": {"state_surface": "kanban",
                              "states": list(fa.REQUIRED_STATES),
                              "transitions": transitions},
            "fanout": {"independent": True, "common_evidence_cutoff": True,
                       "common_reward_dimensions": True, "max_parallel": 6,
                       "fan_in": "group-chat", "evaluator_profile": "challenger",
                       "meeting_triggers": sorted(fa.MEETING_TRIGGERS)},
            "persistence": {"internal_artifacts": "forbidden",
                            "allowed": ["configuration", "skill", "tool",
                                        "mission-deliverable", "native-state",
                                        "convergence-ledger"],
                            "mission_deliverable_requires_operator_locator": True,
                            "retain_chain_of_thought": False}}
        self.contract_path = self.home / "fleet-runtime.yaml"
        self.save_contract()
        self.data = {
            "schema": 2, "instance": "synthetic", "scope": fa.SCOPE,
            "accepted_at": stamp(12), "map_version": "v1",
            "contract": "hermes-data/fleet-runtime.yaml",
            "member_connection": {"id": "desktop-real", "kind": "remote",
                                  "label": "My Gateway"},
            "operator_declaration": {"name": "Operator", "signed_at": stamp(11),
                                     "statement": "I accept this crew and map for mission-buildout only"},
            "client_witness": {"observer": "Operator", "observed_at": stamp(8),
                               "physically_seen": True, "room_keys": room_keys,
                               "post_id": "post-1", "reply_id": "reply-1",
                               "reply_profile": "proposer"},
            "independent_assessor": {"name": "Independent", "reviewed_at": stamp(10),
                                     "finding": "pass"},
            "native_events": {
                "role_drills": {role: f"kanban:coordination/card-{role}/event-ready"
                                for role in ROLES},
                "schedule_runs": {role: f"cron:{profile}/qualification/run-1"
                                  for role, profile in ROLES.items()},
                "handoffs": ["kanban:coordination/card-sense/event-handoff"],
                "room_post": "room:id:coordination/post-1",
                "room_reply": "room:id:coordination/reply-1",
                "stop_test": "runtime:stop/test-1",
                "recovery_test": "runtime:recovery/test-1",
                "independent_assessment": "kanban:improvement/card-audit/event-pass",
                "convergence_recall": "ledger:query/test-1",
                "retry_guard": "ledger:guard/test-1"}}
        self.publish()

    def save_contract(self):
        self.contract_path.write_text(yaml.safe_dump(self.contract, sort_keys=False))

    def publish(self):
        self.receipt.write_text(json.dumps(self.data))
        self.receipt.chmod(0o600)

    def decide(self, want=0, *, now=T0 + timedelta(minutes=15)):
        result, rc = fa.inspect(self.instance, now=now)
        self.assertEqual(rc, want, result)
        self.assertEqual(result["read_only"], True)
        self.assertFalse(result["native_enforced"])
        self.assertFalse(result["native_event_locators_resolved"])
        self.assertEqual(result["admitted"], rc == 0)
        return result

    def test_valid_minimal_native_reference_admission_and_read_only_cli(self):
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in self.instance.rglob("*") if path.is_file()}
        result = self.decide()
        self.assertTrue(result["operator_attested"])
        now = datetime.now(timezone.utc)
        self.data["client_witness"]["observed_at"] = (now - timedelta(minutes=4)).isoformat()
        self.data["independent_assessor"]["reviewed_at"] = (now - timedelta(minutes=3)).isoformat()
        self.data["operator_declaration"]["signed_at"] = (now - timedelta(minutes=2)).isoformat()
        self.data["accepted_at"] = (now - timedelta(minutes=1)).isoformat()
        self.publish()
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in self.instance.rglob("*") if path.is_file()}
        completed = subprocess.run(
            [sys.executable, "-B", str(Path(fa.__file__)), "--instance-dir",
             str(self.instance), "--scope", fa.SCOPE, "--json"],
            text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, (completed.stdout, completed.stderr))
        output = json.loads(completed.stdout)
        self.assertTrue(output["admitted"])
        self.assertNotIn("native_events", completed.stdout)
        after = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                 for path in self.instance.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_exactly_six_root_plus_five_and_function_coverage(self):
        self.decide()
        self.contract["personas"] = self.contract["personas"][:-1]
        self.save_contract()
        self.assertEqual(self.decide(1)["reason"], "invalid_personas")

    def test_state_surface_fanout_and_persistence_are_hard_requirements(self):
        mutations = [
            lambda c: c["state_machine"].update({"state_surface": "file"}),
            lambda c: c["fanout"].update({"independent": False}),
            lambda c: c["fanout"].update({"evaluator_profile": "decider"}),
            lambda c: c["persistence"].update({"internal_artifacts": "allowed"}),
            lambda c: c["persistence"].update({"retain_chain_of_thought": True}),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                original = copy.deepcopy(self.contract)
                mutate(self.contract)
                self.save_contract()
                self.decide(1)
                self.contract = original
                self.save_contract()

    def test_native_boards_rooms_shared_skills_and_wiring_required(self):
        board = self.home / "kanban/boards/coordination/board.json"
        board.unlink()
        self.assertEqual(self.decide(1)["reason"], "missing_native_board")
        board.write_text("{}")
        skill = self.home / "fleet-skills/fleet-convergence-learning/SKILL.md"
        skill.unlink()
        self.assertEqual(self.decide(1)["reason"], "missing_shared_skill")
        skill.write_text("# restored\n")
        profile = self.home / "profiles/proposer/config.yaml"
        profile.write_text("skills: {}\n")
        self.assertEqual(self.decide(1)["reason"], "missing_shared_skill_wiring")

    def test_artifact_job_or_handoff_directory_denies(self):
        profile = self.home / "profiles/proposer"
        jobs = {"jobs": [{"id": "bad", "enabled": True, "state": "scheduled",
                           "schedule": {"kind": "cron", "expr": "0 * * * *"},
                           "next_run_at": stamp(30),
                           "prompt": "Write <cycle_dir>/proposal.md"}]}
        (profile / "cron/jobs.json").write_text(json.dumps(jobs))
        self.assertEqual(self.decide(1)["reason"], "artifact_job_active")
        (profile / "cron/jobs.json").write_text('{"jobs": []}')
        (profile / "handoff").mkdir()
        self.assertEqual(self.decide(1)["reason"], "legacy_file_handoff")

    def test_native_event_locator_shapes_and_independent_people(self):
        self.data["native_events"]["handoffs"] = ["file:handoff.md"]
        self.publish()
        self.assertEqual(self.decide(1)["reason"], "invalid_native_events")
        self.data["native_events"]["handoffs"] = ["kanban:coordination/card/event"]
        self.data["independent_assessor"]["name"] = "Operator"
        self.publish()
        self.assertEqual(self.decide(1)["reason"], "invalid_assessor")

    def test_private_single_ledger_row_and_freshness_required(self):
        self.receipt.chmod(0o644)
        self.assertEqual(self.decide(1)["reason"], "unsafe_admission_ledger")
        self.receipt.chmod(0o600)
        self.assertEqual(self.decide(1, now=T0 + timedelta(hours=30))["reason"],
                         "stale_admission")


if __name__ == "__main__":
    unittest.main()
