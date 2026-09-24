"""Read-only, deliberately non-qualifying formation report contracts."""
import contextlib
import io
import json
from pathlib import Path
import os
import stat
import subprocess
import sys
import tempfile
import unittest

from lib import formation_status as fs


class FormationStatusTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.a = self.instance("solo")

    def instance(self, name):
        instance = self.root / name
        instance.mkdir(mode=0o700)
        (instance / "control.env").write_text("HERMES_PROFILE=default\n")
        (instance / "hermes-data").mkdir()
        return instance

    @staticmethod
    def profile(instance, name):
        profile = instance / "hermes-data" / "profiles" / name
        profile.mkdir(parents=True)
        return profile

    @staticmethod
    def receipt(instance, mode, **more):
        path = instance / fs.RECEIPT
        data = {"schema": 1, "instance": instance.name, "mode": mode, **more}
        path.write_text(json.dumps(data))
        path.chmod(0o600)
        return path

    def run_report(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = fs.main(args)
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        return rc, json.loads(out.getvalue())

    def test_unknown_is_never_qualified_and_root_not_counted(self):
        rc, body = self.run_report("--instance-dir", str(self.a))
        self.assertEqual(rc, 0)
        self.assertEqual(body["instances"][0]["status"], "unknown")
        self.assertFalse(body["formation_qualified"])
        self.assertFalse(body["instances"][0]["formation_qualified"])
        self.profile(self.a, "one")
        self.assertEqual(self.run_report("--instance-dir", str(self.a))[1]["instances"][0]["status"], "unknown")
        self.profile(self.a, ".deleted")
        self.profile(self.a, "two")
        rc, body = self.run_report("--instance-dir", str(self.a))
        self.assertEqual((rc, body["instances"][0]["status"]), (1, "unknown-crew"))
        self.assertEqual(body["instances"][0]["named_profiles"], ["one", "two"])

    def test_explicit_solo_never_hides_multi_profile_crew(self):
        self.profile(self.a, "one")
        self.receipt(self.a, "solo", qualified=True)
        self.assertEqual(self.run_report("--instance-dir", str(self.a))[1]["instances"][0]["status"], "solo")
        self.profile(self.a, "two")
        rc, body = self.run_report("--instance-dir", str(self.a))
        self.assertEqual((rc, body["instances"][0]["status"]), (1, "blocked"))
        self.assertFalse(body["instances"][0]["formation_qualified"])

    def test_host_receipt_plus_agent_self_certification_never_qualifies(self):
        self.profile(self.a, "alice")
        self.profile(self.a, "bob")
        self.receipt(self.a, "crew", qualified=True, evidence={"all_personas": True},
                     checks={"operator_visible": True, "independent_cycle": True})
        agent_file = self.a / "hermes-data" / fs.RECEIPT
        agent_file.write_text('{"schema":1,"mode":"solo","qualified":true}')
        rc, body = self.run_report("--instance-dir", str(self.a))
        self.assertEqual((rc, body["instances"][0]["status"]), (1, "pending"))
        self.assertFalse(body["instances"][0]["formation_qualified"])

    def test_invalid_tampered_cross_instance_receipt_fails_closed(self):
        for data in ('{', '{"schema":1,"instance":"other","mode":"solo"}',
                     '{"schema":true,"instance":"solo","mode":"solo"}',
                     '{"schema":1,"instance":"solo","mode":"elite"}'):
            with self.subTest(data=data):
                receipt = self.a / fs.RECEIPT
                receipt.write_text(data)
                receipt.chmod(0o600)
                rc, body = self.run_report("--instance-dir", str(self.a))
                self.assertEqual((rc, body["instances"][0]["status"]), (1, "blocked"))
        receipt.unlink()
        receipt.symlink_to(self.a / "hermes-data" / "agent-receipt.json")
        self.assertEqual(self.run_report("--instance-dir", str(self.a))[0], 1)
        receipt.unlink()
        receipt = self.receipt(self.a, "solo")
        receipt.chmod(0o644)
        self.assertEqual(self.run_report("--instance-dir", str(self.a))[0], 1)
        receipt.chmod(0o600)
        self.a.chmod(0o755)
        self.assertEqual(self.run_report("--instance-dir", str(self.a))[0], 1)

    def test_declare_private_atomic_intent_and_read_only_status(self):
        path = self.a / fs.RECEIPT
        self.assertEqual(fs.declare(self.a, "solo"), "created")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.stat().st_uid, os.geteuid())
        solo = path.read_bytes()
        solo_stat = path.stat()
        self.assertEqual(json.loads(solo), {"schema": 1, "instance": "solo", "mode": "solo"})
        self.assertEqual(fs.declare(self.a, "solo"), "unchanged")
        self.assertEqual((path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns),
                         (solo, solo_stat.st_ino, solo_stat.st_mtime_ns))
        self.profile(self.a, "named")
        # Upgrade first, while there is only one named profile; this must never
        # report solo during add-persona's native creation sequence.
        self.assertEqual(fs.declare(self.a, "crew"), "upgraded")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotEqual(path.stat().st_ino, solo_stat.st_ino)
        self.assertEqual(self.run_report("--instance-dir", str(self.a))[1]["instances"][0]["status"], "pending")
        crew = path.read_bytes()
        self.assertEqual(fs.declare(self.a, "crew"), "unchanged")
        with self.assertRaises(fs.OperationalError):
            fs.declare(self.a, "solo")
        self.assertEqual(path.read_bytes(), crew)
        self.assertFalse(self.run_report("--instance-dir", str(self.a))[1]["formation_qualified"])
        self.assertFalse(list(self.a.glob(".formation-intent-*")))

    def test_declare_rejects_unsafe_or_accepted_receipts_without_clobbering(self):
        path = self.a / fs.RECEIPT
        for invalid in (
                '{"schema":1,"instance":"solo","mode":"solo","accepted":true}',
                '{"schema":1,"instance":"solo","mode":"crew","evidence":{}}',
                '{"schema":1,"instance":"solo","mode":"crew","mode":"solo"}',
                '{"schema":1,"instance":"other","mode":"solo"}',
                '{"schema":1,"instance":"solo","mode":"bogus"}', "{"):
            with self.subTest(invalid=invalid):
                path.write_text(invalid)
                path.chmod(0o600)
                with self.assertRaises(fs.OperationalError):
                    fs.declare(self.a, "crew")
                self.assertEqual(path.read_text(), invalid)
        path.unlink()
        target = self.root / "outside"
        target.write_text("keep")
        path.symlink_to(target)
        with self.assertRaises(fs.OperationalError):
            fs.declare(self.a, "crew")
        self.assertEqual(target.read_text(), "keep")
        path.unlink()
        path = self.receipt(self.a, "solo")
        for permission in (0o644, 0o400):
            path.chmod(permission)
            with self.assertRaises(fs.OperationalError):
                fs.declare(self.a, "crew")
            self.assertEqual(json.loads(path.read_text())["mode"], "solo")
        path.chmod(0o600)
        self.a.chmod(0o755)
        with self.assertRaises(fs.OperationalError):
            fs.declare(self.a, "crew")
        self.assertEqual(json.loads(path.read_text())["mode"], "solo")
        self.a.chmod(0o700)
        alias = self.root / "redirect"
        alias.symlink_to(self.a, target_is_directory=True)
        with self.assertRaises(fs.OperationalError):
            fs.declare(alias, "crew")
        nested = self.root / "redirect-parent"
        nested.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(fs.OperationalError):
            fs.declare(nested / self.a.name, "crew")
        self.assertEqual(json.loads(path.read_text())["mode"], "solo")
        self.assertFalse(list(self.a.glob(".formation-intent-*")))

    def test_declare_cli_is_separate_from_read_only_report(self):
        command = [sys.executable, "-B", str(Path(fs.__file__)), "declare",
                   "--instance-dir", str(self.a), "--mode", "solo"]
        declared = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(declared.returncode, 0, declared.stderr)
        self.assertIn("not qualified", declared.stdout)
        command[-1] = "crew"
        upgraded = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
        self.assertIn("changed from solo to crew", upgraded.stdout)
        failed = subprocess.run(command[:-1] + ["solo"], capture_output=True, text=True, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(json.loads((self.a / fs.RECEIPT).read_text())["mode"], "crew")
        rc, status = self.run_report("--instance-dir", str(self.a))
        self.assertEqual(rc, 1)
        self.assertTrue(status["read_only"])
        self.assertFalse(status["formation_qualified"])

    def test_report_is_read_only_including_aggregate_and_errors(self):
        self.receipt(self.a, "solo")
        b = self.instance("crew")
        self.profile(b, "a")
        self.profile(b, "b")
        watched = [self.a / "control.env", self.a / fs.RECEIPT,
                   b / "control.env"]
        before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}
        rc, body = self.run_report("--instances-dir", str(self.root))
        self.assertEqual(rc, 1)
        self.assertEqual([r["status"] for r in body["instances"]], ["unknown-crew", "solo"])
        self.assertEqual(before, {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in watched})
        self.assertFalse((b / fs.RECEIPT).exists())
        self.assertFalse((b / ".operation.lock").exists())
        (b / "hermes-data").rename(b / "offline")
        rc, body = self.run_report("--instances-dir", str(self.root))
        self.assertEqual(rc, 2)
        self.assertEqual([r["status"] for r in body["instances"]], ["error", "solo"])
        rc, body = self.run_report("--instance-dir", str(self.root / "missing"))
        self.assertEqual(rc, 2)
        self.assertEqual(body["instances"][0]["status"], "error")


if __name__ == "__main__":
    unittest.main()
