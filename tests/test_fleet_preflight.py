"""Unit fixtures test acceptance rules, never certify native runtime behavior."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import fleet_preflight as f

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.expected = {"runtime_sha256": "a" * 64}
        self.record = {"schema": 1, "persona": "alice", "producer": "verify-fleet-runtime",
                       "scope": "native-end-to-end", "created_at": datetime.now(timezone.utc).isoformat(),
                       "binding": self.expected.copy(), "checks": {
                           "fleet_skills_visible": {"ok": True, "names": ["fleet"], "call_ids": ["c"]},
                           "approvals": {"ok": True, "mode": "off", "pending": 0}}}
        for name in ("foreground_terminal", "cron_execute_code", "cron_dangerous_shell", "single_query_dangerous_shell", "api_dangerous_shell"):
            self.record["checks"][name] = {"ok": True, "call_id": "c", "session_id": "s", "stdout": "nonce\n",
                "expected_stdout": "nonce", "exit_code": 0, "job_id": "j", "cleanup_ok": True,
                "classified_dangerous": True, "classified_hardline": False,
                "arguments": {"timeout": 10, "background": False}}
    def validate(self):
        return f.validate_evidence(self.record, self.expected, "alice", ["fleet"])
    def test_fixture_schema_accepted_not_runtime_certification(self):
        self.assertEqual(self.validate(), [])
    def test_missing_checks_fail(self):
        for name in list(self.record["checks"]):
            with self.subTest(name=name):
                original = self.record["checks"].pop(name)
                self.assertTrue(self.validate())
                self.record["checks"][name] = original
    def test_stale_future_wrong_persona_and_source_fail(self):
        for key, value in (("persona", "other"), ("scope", "mock"), ("binding", {}),
                           ("created_at", (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()),
                           ("created_at", (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())):
            with self.subTest(key=key):
                old = self.record[key]; self.record[key] = value
                self.assertTrue(self.validate()); self.record[key] = old
    def test_shell_stdout_and_arguments_strict(self):
        shell = self.record["checks"]["foreground_terminal"]
        for key, value in (("stdout", ""), ("exit_code", False), ("call_id", ""), ("expected_stdout", ""),
                           ("arguments", {"background": True, "timeout": 10}),
                           ("arguments", {"background": False, "timeout": 10, "heartbeat": 1})):
            with self.subTest(key=key):
                old = shell[key]; shell[key] = value
                self.assertTrue(self.validate()); shell[key] = old
    def test_pending_approval_and_hardline_fail(self):
        self.record["checks"]["approvals"]["pending"] = 1
        self.assertTrue(self.validate())
        self.record["checks"]["approvals"]["pending"] = 0
        self.record["checks"]["api_dangerous_shell"]["classified_hardline"] = True
        self.assertTrue(self.validate())
    def test_malformed_fail_closed(self):
        for value in (None, [], "bad", {"checks": []}, {"checks": {"fleet_skills_visible": []}}):
            self.assertTrue(f.validate_evidence(value, self.expected, "alice", ["fleet"]))

class FilesystemTests(unittest.TestCase):
    def test_binding_changes_on_config_env_skills_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); home = root / "home"; home.mkdir()
            skills = root / "skills"; skills.mkdir(); (skills / "SKILL.md").write_text("skill")
            defaults = root / "defaults.yaml"; defaults.write_text("version: 1")
            initial = f.binding(home, "default", defaults, skills, "a"*64)
            (home / "config.yaml").write_text("approvals: {}")
            self.assertNotEqual(initial, f.binding(home, "default", defaults, skills, "a"*64))
            before = {str(p): p.stat().st_mtime_ns for p in root.rglob("*")}
            f.binding(home, "default", defaults, skills, "a"*64)
            self.assertEqual(before, {str(p): p.stat().st_mtime_ns for p in root.rglob("*")})
    def test_env_is_data_never_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "control.env"
            path.write_text("NAME='a b'\nEMPTY=\n# hello\n")
            self.assertEqual(f.read_env(path), {"NAME": "a b", "EMPTY": ""})
            path.write_text("NAME=$(touch /tmp/nope)\n")
            with self.assertRaises(ValueError): f.read_env(path)
    def test_runtime_fingerprint_covers_source_and_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "tools").mkdir()
            (root / "tools/terminal_tool.py").write_text("first")
            original = f.runtime_fingerprint(root)
            (root / "approval_context.py").write_text("second")
            self.assertNotEqual(original, f.runtime_fingerprint(root))
    def test_full_check_is_read_only_and_missing_evidence_fails(self):
        import argparse
        import shutil
        import apply_stack as stack
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"; home.mkdir()
            (home / "profiles/alice").mkdir(parents=True)
            defaults_path = repo / "stack-defaults.yaml"
            defaults = stack._load_yaml(defaults_path)
            stack.process(str(home), defaults, "https://example.invalid/v1", "test-secret",
                          "default", "apply", stack.Reporter(quiet=True))
            shutil.copytree(repo / "skills", home / "fleet-skills")
            args = argparse.Namespace(home=str(home), defaults=str(defaults_path),
                base_url="https://example.invalid/v1", api_key="test-secret", launch_profile="default",
                policy_only=False, skills_dir=str(repo / "skills"), evidence_dir=None,
                runtime_fingerprint="a"*64, max_evidence_age=86400)
            def snapshot():
                return {str(p.relative_to(home)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
                        for p in home.rglob("*")}
            before = snapshot()
            with patch.object(f.subprocess, "run", side_effect=AssertionError("no native calls during check")):
                result = f.check(args)
            self.assertEqual(before, snapshot())
            self.assertFalse(result["ready"])
            self.assertNotIn("test-secret", json.dumps(result))
            self.assertEqual([r["persona"] for r in result["checks"] if r["check"] == "runtime_evidence"], ["default", "alice"])
            self.assertTrue(all(r["status"] == "PASS" for r in result["checks"] if r["check"] == "stack"))
            shadow = home / "skills/fleet-organism-design"; shadow.mkdir(parents=True)
            (shadow / "SKILL.md").write_text("obsolete")
            result = f.check(args)
            self.assertTrue(any(r["status"] == "FAIL" for r in result["checks"] if r["check"] == "skill:fleet-organism-design"))

    def test_installed_and_external_fleet_content_invalidate_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); home = root / "home"; home.mkdir()
            skills = root / "skills"; (skills / "fleet").mkdir(parents=True)
            (skills / "fleet/SKILL.md").write_text("source")
            config = home / "config.yaml"
            config.write_text("skills: {external_dirs: [/opt/data/fleet-skills, /opt/data/custom]}\n")
            defaults = root / "defaults.yaml"; defaults.write_text("version: 1")
            for dirname in ("fleet-skills", "custom"):
                (home / dirname / "fleet").mkdir(parents=True)
                (home / dirname / "fleet/SKILL.md").write_text("first")
            initial = f.binding(home, "default", defaults, skills, "a"*64)
            (home / "fleet-skills/fleet/SKILL.md").write_text("second")
            changed = f.binding(home, "default", defaults, skills, "a"*64)
            self.assertNotEqual(initial, changed)
            (home / "custom/fleet/SKILL.md").write_text("third")
            self.assertNotEqual(changed, f.binding(home, "default", defaults, skills, "a"*64))
            config.write_text("skills: {external_dirs: [/elsewhere]}\n")
            self.assertIn("unverifiable", f.external_skill_hashes(home, home, skills).values())
            config.write_text("skills: {external_dirs: [/opt/data/../escape]}\n")
            self.assertIn("unverifiable", f.external_skill_hashes(home, home, skills).values())
            config.write_text("skills: {external_dirs: [/opt/data/missing]}\n")
            self.assertIn("unverifiable", f.external_skill_hashes(home, home, skills).values())
            config.write_text("skills: {external_dirs: [/opt/data/custom]}\n")
            with patch.object(f, "tree_hash", side_effect=PermissionError("unreadable fixture")):
                self.assertIn("unverifiable", f.external_skill_hashes(home, home, skills).values())
            record = {"schema": 1, "persona": "default", "producer": "verify-fleet-runtime",
                      "scope": "native-end-to-end", "binding": initial,
                      "created_at": datetime.now(timezone.utc).isoformat(), "checks": {}}
            current = f.binding(home, "default", defaults, skills, "a"*64)
            self.assertTrue(any("binding mismatch" in error for error in
                                f.validate_evidence(record, current, "default", ["fleet"])))

    def test_native_usage_bookkeeping_stable_but_skill_assets_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "fleet").mkdir()
            (root / "fleet/SKILL.md").write_text("canonical")
            (root / ".bundled_manifest").write_text("fleet:hash")
            initial = f.tree_hash(root)
            (root / ".usage.json").write_text('{"view_count": 1}')
            (root / ".usage.json.lock").write_text("locked")
            self.assertEqual(initial, f.tree_hash(root))
            (root / ".usage.json").write_text('{"view_count": 2}')
            self.assertEqual(initial, f.tree_hash(root))
            for relative in ("fleet/SKILL.md", "fleet/asset.txt", ".bundled_manifest", ".other", "fleet/.usage.json"):
                with self.subTest(relative=relative):
                    path = root / relative
                    existed = path.exists()
                    original = path.read_bytes() if existed else None
                    before = f.tree_hash(root)
                    path.write_text("changed")
                    self.assertNotEqual(before, f.tree_hash(root))
                    if existed: path.write_bytes(original)
                    else: path.unlink()

    def test_missing_home_json_failure_no_subprocess(self):
        with patch.object(f.subprocess, "run", side_effect=AssertionError("must not execute")):
            with patch("builtins.print"):
                self.assertEqual(f.main(["--home", "/nonexistent-preflight-home", "--json"]), 2)

if __name__ == "__main__": unittest.main()
