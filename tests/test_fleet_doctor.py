"""Unit tests for a disk-only fleet doctor; no live Hermes state is opened."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import yaml

import fleet_doctor as doctor

NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def make_instance(root: Path, name: str = "test-fleet") -> Path:
    inst = root / name
    home = inst / "hermes-data"
    (home / "profiles" / "alice" / "cron").mkdir(parents=True)
    (home / "cron").mkdir()
    (inst / "control.env").write_text("COMPOSE_PROJECT_NAME=hermes-test\n")
    (home / "config.yaml").write_text("cron: {}\n")
    (home / "profile.yaml").write_text("ui_meta: {}\n")
    persona = home / "profiles" / "alice"
    (persona / "config.yaml").write_text("cron: {}\n")
    (persona / "profile.yaml").write_text("ui_meta: {}\n")
    return inst


def job(kind: str = "cron") -> dict:
    return {"id": "job-1", "name": "bounded task", "enabled": True,
            "state": "scheduled", "schedule": {"kind": kind, "expr": "0 */4 * * *"},
            "next_run_at": (NOW + timedelta(hours=1)).isoformat(),
            "deliver": "local", "failure_deliver": "local"}


def put_jobs(inst: Path, *jobs: dict, profile: str = "alice") -> None:
    p = inst / "hermes-data" / "profiles" / profile / "cron" / "jobs.json"
    p.write_text(json.dumps({"jobs": list(jobs)}))


def findings(report: dict, name: str) -> list[dict]:
    return [r for r in report["checks"] if r["check"] == name]


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.instance = make_instance(self.root)

    def run_doc(self, **kwargs):
        return doctor.run(instance_dir=self.instance, now=NOW, **kwargs)

    def test_basic_read_only_warns_without_pretending_delivered(self):
        put_jobs(self.instance, job())
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        self.assertEqual(data["scope"], "disk-only-no-db-no-runtime")
        self.assertEqual(findings(data, "schedule")[0]["status"], "PASS")
        self.assertEqual(findings(data, "delivery_route")[0]["status"], "WARN")
        self.assertEqual(findings(data, "unknown_outcomes")[0]["status"], "UNVERIFIED")
        self.assertEqual(findings(data, "token_budget")[0]["status"], "UNVERIFIED")
        self.assertEqual(findings(data, "client_visibility")[0]["status"], "UNVERIFIED")

    def test_missing_kind_or_next_run_is_proven_blocker(self):
        j = job()
        del j["schedule"]["kind"]
        j["next_run_at"] = None
        put_jobs(self.instance, j)
        data, rc = self.run_doc()
        self.assertEqual(rc, 1)
        self.assertEqual(findings(data, "schedule")[0]["status"], "FAIL")
        self.assertEqual(findings(data, "next_run_at")[0]["status"], "FAIL")

    def test_paused_and_disabled_jobs_are_not_armed(self):
        a, b = job(), job()
        a["id"], b["id"] = "paused", "disabled"
        a["state"], b["enabled"] = "paused", False
        a["next_run_at"] = b["next_run_at"] = None
        del a["schedule"]["kind"]
        del b["schedule"]["kind"]
        put_jobs(self.instance, a, b)
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        self.assertFalse(findings(data, "schedule"))
        self.assertFalse(findings(data, "next_run_at"))
        self.assertIn("0 active", findings(data, "job_manifest")[1]["detail"])

    def test_missing_enabled_defaults_true_but_explicit_bad_type_errors(self):
        a = job()
        del a["enabled"]
        a["next_run_at"] = None
        put_jobs(self.instance, a)
        data, rc = self.run_doc()
        self.assertEqual(rc, 1)
        self.assertEqual(findings(data, "next_run_at")[0]["status"], "FAIL")
        a["enabled"] = "false"
        put_jobs(self.instance, a)
        _, rc = self.run_doc()
        self.assertEqual(rc, 2)

    def test_paused_at_also_makes_job_dormant(self):
        a = job()
        a["paused_at"] = NOW.isoformat()
        a["next_run_at"] = None
        put_jobs(self.instance, a)
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        self.assertEqual([], findings(data, "next_run_at"))

    def test_timezone_aware_next_run_required_and_overdue_warn(self):
        a = job(); a["next_run_at"] = "2026-09-23T00:00:00" # no timezone
        put_jobs(self.instance, a)
        data, rc = self.run_doc()
        self.assertEqual(rc, 1)
        self.assertEqual(findings(data, "next_run_at")[0]["status"], "FAIL")
        a["next_run_at"] = (NOW - timedelta(hours=1)).isoformat()
        put_jobs(self.instance, a)
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        self.assertEqual(findings(data, "overdue")[0]["status"], "WARN")

    def test_script_accepts_absolute_inside_scripts_and_ignores_shebang(self):
        home = self.instance / "hermes-data" / "profiles" / "alice"
        scripts = home / "scripts"; scripts.mkdir()
        script = scripts / "job.py"; script.write_text("#!/unavailable/interpreter\npass\n")
        a = job(); a.update(script=str(script), no_agent=True)
        put_jobs(self.instance, a)
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        self.assertEqual(findings(data, "script")[0]["status"], "PASS")
        a["script"] = "../profile.yaml"
        put_jobs(self.instance, a)
        data, rc = self.run_doc()
        self.assertEqual(rc, 1)
        self.assertEqual(findings(data, "script")[0]["status"], "FAIL")

    def test_legacy_handoff_directory_is_a_proven_artifact_culture_failure(self):
        put_jobs(self.instance, job())
        (self.instance / "hermes-data" / "profiles" / "alice" / "handoff").mkdir()
        data, rc = self.run_doc()
        self.assertEqual(rc, 1)
        rows = [row for row in findings(data, "artifact_culture")
                if row["profile"] == "alice" and "handoff path" in row["detail"]]
        self.assertEqual([row["status"] for row in rows], ["FAIL"])

    def test_malformed_manifest_errors_rc2(self):
        bad = self.instance / "hermes-data" / "profiles" / "alice" / "cron" / "jobs.json"
        bad.write_text("{oops")
        result, rc = self.run_doc()
        self.assertEqual(rc, 2)
        self.assertIn("error", result)
        put_jobs(self.instance, job())
        bad.write_text("[]")
        _, rc = self.run_doc()
        self.assertEqual(rc, 2)

    def test_internal_artifact_job_fails_and_native_flow_job_passes(self):
        artifact = job()
        artifact["prompt"] = "Write your nowcast to <cycle_dir>/scout/nowcast.md and retro.md."
        put_jobs(self.instance, artifact)
        data, rc = self.run_doc()
        self.assertEqual(rc, 1)
        self.assertEqual(findings(data, "artifact_culture")[0]["status"], "FAIL")

        native = job()
        native["prompt"] = ("Observe the live source, recall bounded convergence patterns, then "
                            "advance the owning Kanban card and post decision-relevant facts in the room.")
        native["durable_output"] = {"class": "native_state"}
        put_jobs(self.instance, native)
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        self.assertEqual(findings(data, "artifact_culture")[0]["status"], "PASS")

    def test_real_mission_deliverable_needs_operator_locator(self):
        delivery = job()
        delivery["prompt"] = "Produce the requested report and write it to /opt/data/report.md."
        delivery["durable_output"] = {"class": "mission_deliverable",
                                      "path": "/opt/data/report.md",
                                      "operator_request": "kanban:board/card-42"}
        put_jobs(self.instance, delivery)
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        self.assertEqual(findings(data, "artifact_culture")[0]["status"], "PASS")
        delivery["durable_output"]["operator_request"] = "trust me"
        put_jobs(self.instance, delivery)
        data, rc = self.run_doc()
        self.assertEqual(rc, 1)
        self.assertIn("scheme:locator", findings(data, "artifact_culture")[0]["detail"])

    def test_all_aggregate_one_report_and_failure(self):
        second = make_instance(self.root, "other")
        put_jobs(self.instance, job())
        bad = job(); bad["next_run_at"] = None
        put_jobs(second, bad)
        data, rc = doctor.run(instances_dir=self.root, now=NOW)
        self.assertEqual(rc, 1)
        self.assertEqual(data["instances"], ["other", "test-fleet"])
        self.assertEqual(data["summary"]["FAIL"], 1)

    def test_does_not_open_sqlite_or_change_instance(self):
        import sqlite3
        p = self.instance / "hermes-data" / "profiles" / "alice" / "cron" / "executions.db"
        p.write_bytes(b"not a database")
        put_jobs(self.instance, job())
        files_before = {str(x.relative_to(self.instance)): (x.stat().st_size, x.stat().st_mtime_ns)
                        for x in self.instance.rglob("*") if x.is_file()}
        with patch.object(sqlite3, "connect", side_effect=AssertionError("SQLite opened")):
            data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        self.assertEqual(findings(data, "unknown_outcomes")[0]["status"], "UNVERIFIED")
        files_after = {str(x.relative_to(self.instance)): (x.stat().st_size, x.stat().st_mtime_ns)
                       for x in self.instance.rglob("*") if x.is_file()}
        self.assertEqual(files_before, files_after)

    def test_desktop_registry_not_backend_room_and_not_proof(self):
        profile = self.instance / "hermes-data" / "profiles" / "alice" / "profile.yaml"
        profile.write_text('ui_meta:\n  hermes-bots-groups:\n    rooms:\n      "name:Local": {roomId: null, log: [{text: hi}]}\n')
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        row = next(r for r in findings(data, "desktop_registry") if r["profile"] == "alice")
        self.assertEqual(row["status"], "UNVERIFIED")
        self.assertIn("1 Desktop", row["detail"])
        self.assertIn("not verified", row["detail"])


    def seat_room(self, *connection_ids: str, key: str = "id:room-1") -> None:
        """Save one Desktop ui_meta room whose remote seats use the given ids."""
        members = [{"name": f"bot{n}", "connectionId": cid, "connectionKind": "remote",
                    "connectionLabel": "gw", "sourceScoped": True}
                   for n, cid in enumerate(connection_ids)]
        profile = self.instance / "hermes-data" / "profiles" / "alice" / "profile.yaml"
        profile.write_text(yaml.safe_dump({"ui_meta": {"hermes-bots-groups": {"rooms": {
            key: {"name": "Room", "roomId": key[3:], "revision": 4,
                  "members": members, "log": [{"text": "hi"}]}}}}}, sort_keys=False))

    def identity_rows(self, **kwargs):
        data, rc = self.run_doc(**kwargs)
        return [r for r in findings(data, "room_connection_identity")], rc

    def test_room_seat_connection_ids_are_unverified_without_operator_input(self):
        """The host cannot read the operator's Desktop registry, so a saved seat id
        is reported, never asserted. It must not read as verified or as a pass."""
        self.seat_room("gw-guessed", "gw-guessed")
        rows, rc = self.identity_rows()
        self.assertEqual(rc, 0)
        self.assertEqual([r["status"] for r in rows], ["UNVERIFIED"])
        self.assertIn("gw-guessed", rows[0]["detail"])
        self.assertIn("never derive it from a hostname", rows[0]["detail"])

    def test_seats_off_the_verified_connection_warn_as_filter_hidden(self):
        """A hostname-derived guess seats ghosts: Desktop's gateway filter keeps a
        room only when a seat id equals the selected source id."""
        self.seat_room("gw-hostname-ts-net", "gw-hostname-ts-net")
        rows, rc = self.identity_rows(verified_connection_ids=["gw-real"])
        self.assertEqual(rc, 0)
        self.assertEqual({r["status"] for r in rows}, {"WARN"})
        self.assertIn("not operator-verified", rows[0]["detail"])
        self.assertIn("no seat uses a verified connection id", rows[1]["detail"])

    def test_fully_verified_seats_pass_without_claiming_client_delivery(self):
        self.seat_room("gw-real", "gw-real")
        rows, rc = self.identity_rows(verified_connection_ids=["gw-real", " "])
        self.assertEqual(rc, 0)
        self.assertEqual([r["status"] for r in rows], ["PASS"])
        self.assertIn("still unverified", rows[0]["detail"])

    def test_mixed_seats_warn_even_when_one_seat_is_verified(self):
        self.seat_room("gw-real", "gw-stale")
        rows, rc = self.identity_rows(verified_connection_ids=["gw-real"])
        self.assertEqual(rc, 0)
        self.assertEqual([r["status"] for r in rows], ["WARN"])
        self.assertIn("gw-stale", rows[0]["detail"])

    def test_local_seats_and_tombstones_are_not_connection_findings(self):
        profile = self.instance / "hermes-data" / "profiles" / "alice" / "profile.yaml"
        profile.write_text(yaml.safe_dump({"ui_meta": {"hermes-bots-groups": {"rooms": {
            "id:local-room": {"members": [{"name": "b", "connectionId": "local",
                                           "connectionKind": "local"}], "log": []},
            "id:gone": {"tombstone": True,
                        "members": [{"name": "b", "connectionId": "gw-x",
                                     "connectionKind": "remote"}], "log": []}}}}},
            sort_keys=False))
        rows, rc = self.identity_rows(verified_connection_ids=["gw-real"])
        self.assertEqual(rc, 0)
        self.assertEqual(rows, [])

    def seat_required_room(self, *connection_ids: str) -> None:
        self.seat_room(*connection_ids)
        home = self.instance / "hermes-data"
        (home / "profile.yaml").write_text(
            (home / "profiles" / "alice" / "profile.yaml").read_text())

    def write_projection(self, text: str) -> None:
        rooms = {"name:Existing": {"name": "Existing", "roomId": None,
                                   "members": [], "revision": 9,
                                   "log": [{"id": "m1", "text": text}]}}
        (self.instance / "hermes-data" / "profile.yaml").write_text(
            yaml.safe_dump({"ui_meta": {"hermes-bots-groups": {
                "version": 3, "updatedAt": 1, "rooms": rooms, "deleted": {}}}}))

    def test_gateway_room_capacity_warns_before_room_omission(self):
        # A new gateway/desktop pair accepts this projection, but an old
        # Desktop has <4,000 bytes left and may omit rooms without tombstones.
        self.write_projection("x" * 45_000)
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)  # advisory; current room still present
        rows = findings(data, "desktop_room_capacity")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "WARN")
        self.assertIn("old Desktop (48,000-byte cap", rows[0]["detail"])
        self.assertIn("omit a room without a tombstone", rows[0]["detail"])
        self.assertIn("192000", rows[0]["detail"])
        self.assertIn("262144", rows[0]["detail"])
        self.assertIn("Installed client/gateway versions", rows[0]["detail"])
        self.assertEqual(findings(data, "desktop_registry")[0]["status"], "UNVERIFIED")
        self.write_projection("small")
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        self.assertEqual(findings(data, "desktop_room_capacity")[0]["status"], "PASS")

    def test_gateway_capacity_shows_legacy_gateway_and_new_desktop_separately(self):
        self.write_projection("x" * 80_000)
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        row = findings(data, "desktop_room_capacity")[0]
        self.assertEqual(row["status"], "WARN")
        self.assertIn("old Desktop (48,000-byte cap", row["detail"])
        self.assertIn("old gateway (65,536-character cap)", row["detail"])
        self.assertNotIn("new Desktop may omit", row["detail"])
        self.assertNotIn("new gateway may reject", row["detail"])
        self.assertIn("other incoming ui_meta keys are unverified", row["detail"])
        self.write_projection("x" * 186_000)
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        row = findings(data, "desktop_room_capacity")[0]
        self.assertEqual(row["status"], "WARN")
        self.assertIn("old Desktop (48,000-byte cap", row["detail"])
        self.assertNotIn("new Desktop may omit", row["detail"])
        self.write_projection("x" * 200_000)
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        self.assertIn("new Desktop may omit", findings(data, "desktop_room_capacity")[0]["detail"])
        self.write_projection("x" * 263_000)
        data, rc = self.run_doc()
        self.assertEqual(rc, 0)
        self.assertIn("new gateway may reject", findings(data, "desktop_room_capacity")[0]["detail"])

    def test_gateway_size_matches_desktop_separator_and_unicode_reserve(self):
        self.assertEqual(doctor._desktop_gateway_size({"a": "x"}), 10)
        self.assertEqual(doctor._desktop_gateway_size({"a": "é"}), 15)
        self.assertEqual(doctor._desktop_gateway_size({"a": "🎉"}), 21)
        self.assertEqual(doctor._desktop_gateway_size({"a": "\x7f"}), 15)
        # Count the full ui_meta incoming wrapper, not just the registry value.
        registry = {"rooms": {"name:é": {"log": ["\x7f🎉"]}}}
        self.assertEqual(doctor._gateway_ui_meta_size(registry),
                         len(json.dumps({"hermes-bots-groups": registry})))
        self.assertGreater(doctor._gateway_ui_meta_size(registry),
                           len(json.dumps(registry)))

    def test_required_room_seats_need_exact_operator_supplied_identity(self):
        key = "id:room-1"
        required = [f"{key}=gw-real"]
        self.seat_required_room("gw-real", "gw-real")
        data, rc = self.run_doc(require_room_connection=required,
                                verified_connection_ids=["gw-real"])
        self.assertEqual(rc, 0)
        self.assertEqual(data["required_room_connections"], {key: "gw-real"})
        self.assertEqual(findings(data, "required_room_seats")[0]["status"], "PASS")
        self.assertIn("human check", findings(data, "required_room_seats")[0]["detail"])
        self.seat_required_room("gw-guessed", "gw-guessed")
        data, rc = self.run_doc(require_room_connection=required,
                                verified_connection_ids=["gw-real"])
        self.assertEqual(rc, 1)
        self.assertEqual(findings(data, "required_room_seats")[0]["status"], "FAIL")
        self.seat_required_room("gw-real", "gw-guessed")
        data, rc = self.run_doc(require_room_connection=required,
                                verified_connection_ids=["gw-real"])
        self.assertEqual(rc, 1)
        self.assertEqual(findings(data, "required_room_seats")[0]["status"], "FAIL")

    def test_required_room_missing_empty_local_or_duplicate_seats_fail(self):
        required = ["id:room-1=gw-real"]
        for ids in ((), ("gw-real",), ("local", "local")):
            self.seat_required_room(*ids)
            data, rc = self.run_doc(require_room_connection=required,
                                    verified_connection_ids=["gw-real"])
            self.assertEqual(rc, 1, ids)
            self.assertEqual(findings(data, "required_room_seats")[0]["status"], "FAIL")
        self.seat_required_room("gw-real", "gw-real")
        profile = self.instance / "hermes-data" / "profile.yaml"
        registry = yaml.safe_load(profile.read_text())
        room = registry["ui_meta"]["hermes-bots-groups"]["rooms"]["id:room-1"]
        room["members"][1]["name"] = room["members"][0]["name"]
        profile.write_text(yaml.safe_dump(registry))
        data, rc = self.run_doc(require_room_connection=required,
                                verified_connection_ids=["gw-real"])
        self.assertEqual(rc, 1)
        self.assertEqual(findings(data, "required_room_seats")[0]["status"], "FAIL")
        data, rc = self.run_doc(require_room_connection=["id:missing=gw-real"],
                                verified_connection_ids=["gw-real"])
        self.assertEqual(rc, 1)
        self.assertEqual(findings(data, "required_room_seats")[0]["status"], "FAIL")
        profile.write_text(yaml.safe_dump({"ui_meta": {"hermes-bots-groups": {
            "rooms": {"id:room-1": {"members": [
                {"name": f"bot{i}", "connectionId": "gw-real",
                 "connectionKind": "remote"} for i in range(7)]}}}}}))
        data, rc = self.run_doc(require_room_connection=required,
                                verified_connection_ids=["gw-real"])
        self.assertEqual(rc, 1)
        self.assertEqual(findings(data, "required_room_seats")[0]["status"], "FAIL")

    def test_required_room_mapping_is_explicit_and_single_instance(self):
        self.seat_required_room("gw-real", "gw-real")
        for args in (["bad"], ["id:room-1=local"],
                     ["id:room-1=gw-real", "id:room-1=gw-wrong"]):
            data, rc = self.run_doc(require_room_connection=args,
                                    verified_connection_ids=["gw-real"])
            self.assertEqual(rc, 2, args)
        data, rc = self.run_doc(require_room_connection=["id:room-1=gw-real"])
        self.assertEqual(rc, 2)  # an unverified assertion cannot be a guard
        data, rc = doctor.run(instances_dir=self.root,
                              require_room_connection=["id:room-1=gw-real"],
                              verified_connection_ids=["gw-real"], now=NOW)
        self.assertEqual(rc, 2)

    def test_verified_ids_are_reported_and_never_make_a_fleet_ready(self):
        self.seat_room("gw-real")
        data, rc = self.run_doc(verified_connection_ids=["gw-real"])
        self.assertEqual(rc, 0)
        self.assertEqual(data["verified_connection_ids"], ["gw-real"])
        client = next(r for r in findings(data, "client_visibility"))
        self.assertEqual(client["status"], "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
