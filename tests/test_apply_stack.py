"""Offline stack reconciliation tests. Run: python3 -m unittest discover -s tests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

from lib import apply_stack as stack


APPROVALS = {
    "mode": "off", "cron_mode": "approve", "single_query_mode": "approve",
    "unattended_mode": "approve", "mcp_reload_confirm": False,
    "destructive_slash_confirm": False,
}
FLEET_DIR = "/opt/data/fleet-skills"


class ApplyStackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.defaults = stack._load_yaml(stack.DEFAULTS_CANDIDATES)
        self.profile = self.home / "profiles" / "research"
        self.profile.mkdir(parents=True)

    def run_stack(self, mode="apply", policy_only=False, launch_profile="default"):
        rep = stack.Reporter(quiet=True)
        stack.process(str(self.home), self.defaults, "http://example.invalid/v1",
                      "test-only-placeholder", launch_profile, mode, rep, policy_only=policy_only)
        return rep

    def config(self, persona="default"):
        return stack._load_yaml(stack.config_path(str(self.home), persona))

    def put_config(self, data, persona="default"):
        Path(stack.config_path(str(self.home), persona)).write_text(stack._dump_yaml(data))

    def snapshot(self):
        return {str(p.relative_to(self.home)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.home.rglob("*") if p.is_file()}

    def test_named_api_keys_unique_preserved_and_status_read_only(self):
        other=self.home/'profiles'/'other'; other.mkdir()
        (self.home/'.env').write_text('API_SERVER_KEY=root-key-must-stay-unchanged\nOTHER_SECRET=keep\n')
        self.run_stack()
        research=(self.profile/'.env').read_text(); other_env=(other/'.env').read_text()
        def key(text): return next(line.split('=',1)[1] for line in text.splitlines() if line.startswith('API_SERVER_KEY='))
        self.assertRegex(key(research),r'^[0-9a-f]{64}$')
        self.assertNotEqual(key(research),key(other_env))
        self.assertEqual(key((self.home/'.env').read_text()),'root-key-must-stay-unchanged')
        self.assertEqual((self.profile/'.env').stat().st_mode & 0o777,0o600)
        before=self.snapshot(); self.run_stack(); self.assertEqual(before,self.snapshot())
        (self.profile/'.env').write_text(research.replace('API_SERVER_KEY='+key(research)+'\n',''))
        before=self.snapshot(); rep=self.run_stack('status'); self.assertEqual(before,self.snapshot())
        self.assertTrue(any('API_SERVER_KEY missing' in message for _,message in rep.issues))
        self.assertNotIn(key(other_env),str(rep.issues))

    def test_existing_named_api_key_never_rotated_even_blank(self):
        self.run_stack()
        for existing in ['existing-profile-secret-keep', '']:
            with self.subTest(existing=bool(existing)):
                path=self.profile/'.env'
                lines=[line for line in path.read_text().splitlines() if not line.startswith('API_SERVER_KEY=')]
                path.write_text('\n'.join(lines)+'\nAPI_SERVER_KEY='+existing+'\n')
                before=path.read_bytes(); rep=self.run_stack()
                self.assertEqual(before,path.read_bytes())
                if not existing: self.assertTrue(any('present but unusable' in message for _,message in rep.issues))

    def test_initial_gateway_reserves_six_personas_then_scales(self):
        self.profile.rmdir()
        self.run_stack()
        initial = self.config()
        self.assertTrue(initial["gateway"]["multiplex_profiles"])
        self.assertTrue(initial["gateway"]["auto_multiplex_migration"])
        self.assertEqual(initial["gateway"]["api_server"]["max_concurrent_runs"], 12)
        self.assertEqual(initial["max_live_sessions"], 14)
        for index in range(5):
            (self.home / "profiles" / f"crew-{index}").mkdir()
            self.run_stack()
            self.assertEqual(self.config()["gateway"], initial["gateway"])
            self.assertEqual(self.config()["max_live_sessions"], 14)
            self.assertFalse(self.run_stack("status").issues)
        (self.home / "profiles" / "crew-six").mkdir()
        self.run_stack()
        self.assertEqual(self.config()["gateway"]["api_server"]["max_concurrent_runs"], 14)
        self.assertEqual(self.config()["max_live_sessions"], 15)
        self.assertEqual(stack.backends_target(self.defaults, 200), (32, 128))

    def test_full_approval_policy_every_profile_and_audit_every_key(self):
        self.assertEqual(self.defaults["approvals"], APPROVALS)
        self.run_stack()
        for persona in ("default", "research"):
            self.assertEqual(self.config(persona)["approvals"], APPROVALS)
        self.assertEqual(self.run_stack("status").issues, [])
        for key, value in APPROVALS.items():
            with self.subTest(key=key):
                cfg = self.config("research")
                cfg["approvals"][key] = not value if isinstance(value, bool) else "ask"
                self.put_config(cfg, "research")
                issues = self.run_stack("status").issues
                self.assertTrue(any(where == "research" and f"approvals.{key} differs" in msg
                                    for where, msg in issues), issues)
                self.run_stack()

    def test_approval_block_passed_through_without_mutating_defaults(self):
        defaults = copy.deepcopy(self.defaults)
        defaults["approvals"]["future_policy"] = "approve"
        patch = stack.build_persona_patch(defaults, "http://example.invalid/v1", "placeholder")
        self.assertEqual(patch["approvals"], defaults["approvals"])
        patch["approvals"]["future_policy"] = "ask"
        self.assertEqual(defaults["approvals"]["future_policy"], "approve")

    def test_external_skill_dirs_preserved_and_missing_fleet_dir_detected(self):
        for persona in ("default", "research"):
            self.put_config({"skills": {"external_dirs": ["/opt/local/skills", FLEET_DIR],
                                        "custom_option": True}}, persona)
        self.run_stack()
        for persona in ("default", "research"):
            skills = self.config(persona)["skills"]
            self.assertEqual(skills["external_dirs"], [FLEET_DIR, "/opt/local/skills"])
            self.assertTrue(skills["custom_option"])
        self.assertFalse(self.run_stack("status").issues)
        cfg = self.config("research")
        cfg["skills"]["external_dirs"] = ["/opt/local/skills"]
        self.put_config(cfg, "research")
        issues = self.run_stack("status").issues
        self.assertTrue(any("skills.external_dirs is missing" in msg for _, msg in issues))
        self.run_stack()
        self.assertIn("/opt/local/skills", self.config("research")["skills"]["external_dirs"])

    def test_scalar_external_skill_dir_preserved(self):
        self.put_config({"skills": {"external_dirs": "/opt/local/skills"}})
        self.run_stack()
        self.assertEqual(self.config()["skills"]["external_dirs"], [FLEET_DIR, "/opt/local/skills"])

    def test_description_only_metadata_gains_shadow_without_losing_fields(self):
        for path in (self.home / "profile.yaml", self.profile / "profile.yaml"):
            path.write_text("description: Custom persona\ndescription_auto: true\nidentity: local\n")
        before = self.snapshot()
        issues = self.run_stack("status").issues
        self.assertEqual(before, self.snapshot())
        self.assertEqual(sum("missing ui_meta" in msg for _, msg in issues), 2)
        self.run_stack()
        for path in (self.home / "profile.yaml", self.profile / "profile.yaml"):
            self.assertEqual(stack._load_yaml(path), {
                "description": "Custom persona", "description_auto": True,
                "identity": "local", "ui_meta": {},
            })
            self.assertTrue(list(path.parent.glob("profile.yaml.bak-stack-*")))

    def test_malformed_existing_ui_meta_is_never_overwritten(self):
        self.run_stack()
        for metadata in ("ui_meta: malformed\n", "- invalid-metadata\n"):
            with self.subTest(metadata=metadata):
                path = self.profile / "profile.yaml"
                path.write_text(metadata)
                for mode in ("status", "apply"):
                    before = self.snapshot()
                    with self.assertRaises(ValueError):
                        self.run_stack(mode)
                    self.assertEqual(before, self.snapshot())

    def test_root_registry_initialized_only_on_apply(self):
        before = self.snapshot()
        issues = self.run_stack("status").issues
        self.assertTrue(any(where == "default" and "missing profile.yaml" in msg
                            for where, msg in issues))
        self.assertEqual(before, self.snapshot())
        rep = self.run_stack()
        root_metadata = self.home / "profile.yaml"
        self.assertIn(str(root_metadata), rep.wrote)
        self.assertEqual(stack._load_yaml(root_metadata), {"ui_meta": {}})
        self.assertFalse(self.run_stack("status").issues)

    def test_drift_does_not_echo_provider_keys_or_env_values(self):
        self.run_stack()
        cfg = self.config()
        cfg["custom_providers"][0]["api_key"] = "private-provider-placeholder"
        cfg["custom_providers"][0]["base_url"] = "http://private-user:private-pass@example.invalid?key=private-query"
        cfg["model"]["base_url"] = "http://private-user:private-pass@example.invalid?key=private-query"
        cfg["auxiliary"]["compression"]["fallback_chain"] = [{"secret": "private-aux-token"}]
        cfg["fallback_model"] = [{"provider": "private-fallback-token", "model": "private-model-token"}]
        cfg["gateway"]["api_server"]["max_concurrent_runs"] = "private-gateway-token"
        self.put_config(cfg)
        (self.home / ".env").write_text("TELEGRAM_REACTIONS=private-env-placeholder\n")
        issues = self.run_stack("status").issues
        report = str(issues)
        self.assertIn("custom_providers", report)
        self.assertIn(".env TELEGRAM_REACTIONS", report)
        self.assertNotIn("private-", report)
        self.assertNotIn("private-provider-placeholder", report)
        self.assertNotIn("private-env-placeholder", report)
        self.assertNotIn("test-only-placeholder", report)

    def test_new_profile_shadow_and_status_read_only(self):
        self.run_stack()
        added = self.home / "profiles" / "new-profile"
        added.mkdir()
        before = self.snapshot()
        issues = self.run_stack("status").issues
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(any(where == "new-profile" and "missing profile.yaml" in msg
                            for where, msg in issues))
        rep = self.run_stack()
        self.assertIn(str(added / "profile.yaml"), rep.wrote)
        self.assertEqual(stack._load_yaml(added / "profile.yaml")["ui_meta"], {})
        self.assertFalse(self.run_stack("status").issues)
        self.assertIn(FLEET_DIR, self.config("new-profile")["skills"]["external_dirs"])

    def test_existing_metadata_root_rooms_and_persona_state_untouched(self):
        metadata = "# Keep Desktop mirroring\nui_meta:\n  hermes-bots-groups:\n    foreign-node: [room-1]\n"
        (self.home / "profile.yaml").write_text(metadata)
        (self.profile / "profile.yaml").write_text(metadata + "description: My custom profile\n")
        (self.home / "MEMORY.md").write_text("Root-only memory")
        (self.profile / "MEMORY.md").write_text("Persona-only memory")
        (self.home / "credentials.json").write_text(json.dumps({"fake": "test-only"}))
        original = self.snapshot()
        self.run_stack()
        self.assertFalse(self.run_stack("status").issues)
        after = self.snapshot()
        for name, value in original.items():
            self.assertEqual(after[name], value)
        self.assertFalse((self.profile / "credentials.json").exists())

    def test_policy_only_preserves_model_and_timeout_pins(self):
        pinned = {
            "model": {"default": "local-model", "api_key": "fake-local-key"},
            "agent": {"reasoning_effort": "max"},
            "compression": {"threshold": 0.9},
            "auxiliary": {"compression": {"model": "local-helper"}},
            "cron": {"model": "local-cron"},
            "custom_providers": [{"name": "local", "base_url": "http://localhost/v1"}],
            "fallback_model": [{"provider": "local", "model": "local-fallback"}],
        }
        for persona in ("default", "research"):
            self.put_config(pinned, persona)
            Path(stack.env_path(str(self.home), persona)).write_text("HERMES_AGENT_TIMEOUT=99\n")
        self.run_stack(policy_only=True)
        for persona in ("default", "research"):
            cfg = self.config(persona)
            for key, value in pinned.items():
                self.assertEqual(cfg[key], value)
            self.assertEqual(cfg["approvals"], APPROVALS)
            self.assertIn(FLEET_DIR, cfg["skills"]["external_dirs"])
            self.assertIn("HERMES_AGENT_TIMEOUT=99", Path(stack.env_path(str(self.home), persona)).read_text())
        self.assertFalse(self.run_stack("status", policy_only=True).issues)
        before = self.snapshot()
        self.assertFalse(self.run_stack(policy_only=True).wrote)
        self.assertEqual(before, self.snapshot())

    def test_named_launch_configures_and_audits_root_without_model_drift(self):
        pinned = {
            "model": {"default": "local-model", "api_key": "fake-local-key"},
            "agent": {"reasoning_effort": "max"},
            "compression": {"threshold": 0.9},
            "auxiliary": {"compression": {"model": "local-helper"}},
            "cron": {"model": "local-cron"},
            "custom_providers": [{"name": "local", "base_url": "http://localhost/v1"}],
            "fallback_model": [{"provider": "local", "model": "local-fallback"}],
        }
        for persona in ("default", "research"):
            cfg = copy.deepcopy(pinned)
            cfg["gateway"] = {"custom_option": "keep"}
            self.put_config(cfg, persona)
            Path(stack.env_path(str(self.home), persona)).write_text("HERMES_AGENT_TIMEOUT=99\n")
        options = {"policy_only": True, "launch_profile": "research"}
        self.run_stack(**options)
        expected, _, live = stack.gateway_patch(self.defaults, 2)
        for persona in ("default", "research"):
            cfg = self.config(persona)
            for key, value in pinned.items():
                self.assertEqual(cfg[key], value)
            for key, value in expected["gateway"].items():
                self.assertEqual(cfg["gateway"][key], value)
            self.assertEqual(cfg["gateway"]["custom_option"], "keep")
            self.assertEqual(cfg["max_live_sessions"], live)
            self.assertIn("HERMES_AGENT_TIMEOUT=99", Path(stack.env_path(str(self.home), persona)).read_text())
        self.assertFalse(self.run_stack("status", **options).issues)
        before = self.snapshot()
        self.assertFalse(self.run_stack(**options).wrote)
        self.assertEqual(before, self.snapshot())
        for persona in ("default", "research"):
            cfg = self.config(persona)
            cfg["gateway"]["multiplex_profiles"] = False
            self.put_config(cfg, persona)
            before = self.snapshot()
            issues = self.run_stack("status", **options).issues
            self.assertTrue(any(where == persona and "gateway.multiplex_profiles" in msg
                                for where, msg in issues), issues)
            self.assertEqual(before, self.snapshot())
            self.run_stack(**options)

    def test_status_cli_operational_errors_are_not_drift(self):
        command = [sys.executable, stack.__file__, "status", "--home", str(self.home),
                   "--preserve-model-stack", "--json"]
        (self.home / "config.yaml").write_text("broken: [private-placeholder\n")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("private-placeholder", result.stderr + result.stdout)
        (self.home / "config.yaml").unlink()
        (self.home / "cron").mkdir()
        (self.home / "cron" / "jobs.json").write_text("{broken-private-jobs")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("private-jobs", result.stderr + result.stdout)
        command[4] = str(self.home / "absent")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_policy_only_cli_needs_no_endpoint(self):
        command = [sys.executable, stack.__file__, "apply", "--home", str(self.home),
                   "--preserve-model-stack", "--json"]
        applied = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        command[2] = "status"
        before = self.snapshot()
        audited = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(audited.returncode, 0, audited.stderr + audited.stdout)
        self.assertEqual(json.loads(audited.stdout)["drift"], [])
        self.assertEqual(before, self.snapshot())
        self.assertNotIn("model", self.config())
        self.assertNotIn("custom_providers", self.config())

    def test_apply_is_idempotent_including_env_and_metadata(self):
        (self.profile / ".env").write_text("# local settings\nLOCAL_SETTING=preserve\nTELEGRAM_REACTIONS=true\n")
        first = self.run_stack()
        self.assertIn(str(self.profile / ".env"), first.wrote)
        before = self.snapshot()
        second = self.run_stack()
        self.assertEqual(second.wrote, [])
        self.assertEqual(self.snapshot(), before)
        self.assertIn("LOCAL_SETTING=preserve", (self.profile / ".env").read_text())
        self.assertFalse(self.run_stack("status").issues)


if __name__ == "__main__":
    unittest.main()
