"""Hermes shell integration; no live containers or instance data are touched."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class FleetShellTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        for name in ("hermes-spawn.sh", "compose.yaml", "stack-defaults.yaml"):
            shutil.copy2(ROOT / name, self.repo / name)
        shutil.copytree(ROOT / "skills", self.repo / "skills")
        (self.repo / "lib").mkdir()
        (self.repo / "lib/apply_stack.py").write_text("# mocked\n")
        (self.repo / "lib/fleet_preflight.py").write_text("# mocked\n")
        shutil.copy2(ROOT / "lib/formation_status.py", self.repo / "lib/formation_status.py")
        self.instance = self.repo / "instances/demo"
        self.home = self.instance / "hermes-data"
        self.home.mkdir(parents=True)
        self.instance.chmod(0o700)  # private staging/rollback must be host-only
        (self.home / "bot_relay").mkdir(mode=0o555)
        control = dict(COMPOSE_PROJECT_NAME="hermes-demo", TAILSCALE_HOSTNAME="demo",
            TAILSCALE_STATE_DIR=str(self.instance / "tailscale-state"),
            TAILSCALE_AUTHKEY_FILE=str(self.instance / "tailscale-authkey"),
            TS_AUTHKEY_SPEC="file:/run/secrets/tailscale-authkey", HERMES_DATA_DIR=str(self.home),
            HERMES_PROFILE="default", HERMES_UID="10000", HERMES_GID="10000",
            HERMES_CPUS="2", HERMES_MEMORY="4g", HERMES_MEMORY_RESERVATION="1g",
            HERMES_SHM_SIZE="1g", HERMES_PIDS_LIMIT="100", HERMES_IMAGE="hermes:test",
            TAILSCALE_IMAGE="tailscale:test", MODEL_STACK_BASE_URL="http://custom:8317/v1",
            MODEL_STACK_API_KEY="fake-key")
        (self.instance / "control.env").write_text("".join(f"{k}={v}\n" for k,v in control.items()))
        self.bin = self.repo / "bin"
        self.bin.mkdir()
        self.log = self.repo / "calls"
        self.env = dict(os.environ, PATH=str(self.bin)+":"+os.environ["PATH"],
                        MOCK_LOG=str(self.log), MOCK_HOME=str(self.home))
        self.script("docker", r"""#!/bin/bash
printf 'docker %s\n' "$*" >> "$MOCK_LOG"
case "$*" in
  *'profile create '*)
    [[ "${MOCK_CREATE_RC:-0}" == 0 ]] || exit "$MOCK_CREATE_RC"
    name="${@: -1}"; mkdir -p "$MOCK_HOME/profiles/$name"
    if [[ "${MOCK_METADATA:-}" == yes ]]; then echo 'ui_meta: {keep: true}' > "$MOCK_HOME/profiles/$name/profile.yaml"; fi ;;
  *'ps --status running --services'*) echo hermes ;;
  *'compose --env-file '*' pull'*) exit "${MOCK_PULL_RC:-0}" ;;
  *'read_remote_roster'*) echo 0 ;;
  *'Native gateway hot-serving verified'*) exit "${MOCK_RESCAN_RC:-0}" ;;
  *'Native fleet skill discovery/read verified'*) exit "${MOCK_NATIVE_RC:-0}" ;;
esac
""")
        self.script("curl", '#!/bin/sh\nexit 0\n')
        # Spawn fixtures stop after the first compose call, but log earlier
        # declaration order and never reach any real Docker or model.
        self.script("openssl", '#!/bin/sh\nprintf "mock-key\n"\n')
        self.script("cp", r"""#!/bin/bash
if [[ "${MOCK_CP_FAIL:-}" == 1 ]]; then exit 72; fi
/bin/cp "$@" || exit $?
if [[ "${MOCK_CP_TAMPER:-}" == 1 && "$*" == *'/skills/fleet-organism-design '* ]]; then
  dest="${@: -1}"
  printf '\nCORRUPTED\n' >> "$dest/SKILL.md"
fi
""")
        self.script("python3", r"""#!/bin/bash
printf 'python3 %s\n' "$*" >> "$MOCK_LOG"
if [[ "$1" == -B && "$2" == - ]]; then
  if [[ "${MOCK_PUBLISH_FAIL:-}" == 1 ]]; then exit 71; fi
  exec /usr/bin/python3 "$@"
fi
case "$*" in
  *fleet_preflight.py*) printf '{"ok":true}\n'; exit "${MOCK_PREFLIGHT_RC:-0}" ;;
  *fleet_doctor.py*) printf '{"read_only":true}\n'; exit "${MOCK_AUDIT_RC:-0}" ;;
  *'formation_status.py declare '*)
    if [[ "${MOCK_DECLARE_RC:-0}" != 0 ]]; then exit "$MOCK_DECLARE_RC"; fi
    exec /usr/bin/python3 "$@" ;;
  *formation_status.py*) printf '{"read_only":true}\n'; exit "${MOCK_AUDIT_RC:-0}" ;;
  *'apply_stack.py status'*) exit "${MOCK_STATUS_RC:-0}" ;;
  *'apply_stack.py apply'*) exit "${MOCK_APPLY_RC:-0}" ;;
esac
""")
        # The container device prerequisite is unrelated to dispatch behavior.
        shell = (self.repo / "hermes-spawn.sh").read_text()
        shell = shell.replace('[[ -c /dev/net/tun ]]', '[[ -d /dev ]]')
        (self.repo / "hermes-spawn.sh").write_text(shell)

    def script(self, name, text):
        path=self.bin/name; path.write_text(text); path.chmod(0o755)

    def run_cli(self, *args, input=None, **env):
        return subprocess.run(["bash", str(self.repo/"hermes-spawn.sh"), *args],
            env=dict(self.env, **env), input=input, text=True, capture_output=True, timeout=15)

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_readonly_fleet_doctor_and_formation_status_dispatch(self):
        policy = self.repo / "sample policy.json"
        policy.write_text('{"schema":1,"handoff_profiles":{}}')
        for command in ("fleet-doctor", "formation-status"):
            for scope in ("demo", "all"):
                for code in (0, 1, 2):
                    opts = ("--json", "--policy-file", str(policy)) if command == "fleet-doctor" else ()
                    result = self.run_cli(command, scope, *opts, MOCK_AUDIT_RC=str(code))
                    self.assertEqual(result.returncode, code, (command, scope, result.stderr))
                    self.assertEqual(json.loads(result.stdout), {"read_only": True})
                    call = self.calls().splitlines()[-1]
                    self.assertIn("--instances-dir" if scope == "all" else "--instance-dir", call)
                    if command == "fleet-doctor":
                        self.assertIn("--policy-file", call)
                        self.assertIn(str(policy), call)
            for bad_args in ((), ("../demo",), ("/tmp/nope",)):
                result = self.run_cli(command, *bad_args)
                self.assertNotEqual(result.returncode, 0)
            if command == "formation-status":
                self.assertNotEqual(self.run_cli(command, "demo", "--json").returncode, 0)
        self.assertFalse((self.instance / ".operation.lock").exists())
        self.assertFalse((self.repo / "instances/.supervisor.log").exists())
        self.assertNotIn("docker", self.calls())

    def test_preflight_read_only_json_and_failure(self):
        for rc in (0, 1, 2):
            result=self.run_cli("preflight", "demo", "--json", MOCK_PREFLIGHT_RC=str(rc))
            self.assertEqual(result.returncode, rc, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"ok": True})
        self.assertNotIn("docker",self.calls())
        self.assertFalse((self.instance/".operation.lock").exists())
        self.assertIn("--instance-dir", self.calls())

    def test_add_persona_clean_and_root_skills_untouched(self):
        root_skill=self.home/"skills/mine/SKILL.md"
        root_skill.parent.mkdir(parents=True); root_skill.write_text("user content")
        result=self.run_cli("add-persona", "demo", "alice")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("hermes profile create alice", self.calls())
        self.assertNotIn("restart hermes",self.calls()); self.assertNotIn("--clone",self.calls()); self.assertNotIn("profile use",self.calls())
        self.assertIn("ui_meta: {}",(self.home/"profiles/alice/profile.yaml").read_text())
        self.assertEqual(root_skill.read_text(),"user content")
        receipt = self.instance / "formation-acceptance.json"
        self.assertEqual(json.loads(receipt.read_text()),
                         {"schema": 1, "instance": "demo", "mode": "crew"})
        self.assertTrue((self.home/"fleet-skills/fleet-organism-design/SKILL.md").exists())
        self.assertIn("Native fleet skill discovery/read verified", self.calls())

    def test_add_persona_declares_crew_before_native_create(self):
        receipt = self.instance / "formation-acceptance.json"
        receipt.write_text(json.dumps({"schema": 1, "instance": "demo", "mode": "solo"}))
        receipt.chmod(0o600)
        self.home.joinpath("profiles", "first").mkdir(parents=True)
        result = self.run_cli("add-persona", "demo", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changed from solo to crew", result.stdout)
        self.assertEqual(json.loads(receipt.read_text())["mode"], "crew")
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        calls = self.calls()
        self.assertLess(calls.index("formation_status.py declare"), calls.index("hermes profile create alice"))
        self.assertEqual((self.home / "profiles/first").is_dir(), True)
        result = self.run_cli("add-persona", "demo", "bob")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(receipt.read_text())["mode"], "crew")
        self.assertNotIn("changed from solo to crew", result.stdout)

    def test_add_persona_blocked_on_unsafe_receipt_before_native_create(self):
        receipt = self.instance / "formation-acceptance.json"
        for content in (
                '{"schema":1,"instance":"demo","mode":"solo","accepted":true}',
                "INVALID"):
            with self.subTest(content=content):
                receipt.write_text(content)
                receipt.chmod(0o600)
                result = self.run_cli("add-persona", "demo", "alice")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(receipt.read_text(), content)
                self.assertNotIn("profile create alice", self.calls())
                self.assertFalse((self.home / "profiles/alice").exists())
        receipt.unlink()
        target = self.repo / "outside"
        target.write_text("keep")
        receipt.symlink_to(target)
        failed = self.run_cli("add-persona", "demo", "alice")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(target.read_text(), "keep")
        self.assertNotIn("profile create alice", self.calls())
        receipt.unlink()
        failed = self.run_cli("add-persona", "demo", "alice", MOCK_DECLARE_RC="83")
        self.assertNotEqual(failed.returncode, 0)
        self.assertNotIn("profile create alice", self.calls())
        self.assertFalse(receipt.exists())

    def test_spawn_declares_wizard_mode_before_compose_or_profile_create(self):
        # A shared auth-key avoids secrets from stdin, while the mock Docker
        # pull deliberately stops the flow after the declaration is written.
        key = self.repo / ".tailscale-authkey"
        key.write_text("tskey-fixture")
        key.chmod(0o600)
        for instance, persona, answer, mode in (
                ("wizard-solo", "alice", "n", "solo"),
                ("wizard-crew", "alice", "y", "crew"),
                ("wizard-default", "default", "", "solo")):
            with self.subTest(instance=instance):
                self.log.write_text("")
                # hostname, profile, optional multi-persona question, cpu,
                # memory; no provider wizard or real container is started.
                lines = [instance, persona] + ([answer] if persona != "default" else [])
                lines += ["", ""]
                result = self.run_cli("spawn", input="\n".join(lines) + "\n", MOCK_PULL_RC="77")
                self.assertEqual(result.returncode, 77, result.stderr)
                target = self.repo / "instances" / instance
                receipt = target / "formation-acceptance.json"
                self.assertEqual(json.loads(receipt.read_text()),
                                 {"schema": 1, "instance": instance, "mode": mode})
                self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
                calls = self.calls()
                self.assertLess(calls.index("formation_status.py declare"), calls.index(" pull"))
                self.assertNotIn("profile create", calls)
                self.assertNotIn("hot-serving", calls)
                self.assertNotIn("formation_qualified", receipt.read_text())
        self.log.write_text("")
        failed = self.run_cli("spawn", input="wizard-fail\nalice\ny\n\n\n",
                              MOCK_DECLARE_RC="88", MOCK_PULL_RC="77")
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse((self.repo / "instances/wizard-fail/formation-acceptance.json").exists())
        self.assertNotIn(" pull", self.calls())
        self.assertNotIn("profile create", self.calls())

    def test_add_persona_keeps_native_metadata(self):
        result=self.run_cli("add-persona","demo","alice",MOCK_METADATA="yes")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.home/"profiles/alice/profile.yaml").read_text(),"ui_meta: {keep: true}\n")

    def test_add_persona_failure_codes(self):
        for flag in ("MOCK_CREATE_RC", "MOCK_APPLY_RC", "MOCK_NATIVE_RC", "MOCK_RESCAN_RC"):
            with self.subTest(flag=flag):
                name=flag.lower()
                result=self.run_cli("add-persona","demo",name,**{flag:"7"})
                self.assertEqual(result.returncode,7,result.stderr)

    def test_all_and_reserved_names_rejected(self):
        for args in (("all","alice"),("demo","default"),("demo","../escape")):
            result=self.run_cli("add-persona",*args)
            self.assertNotEqual(result.returncode,0)
        self.assertNotIn("profile create",self.calls())

    def test_stack_status_exit_contract_and_no_lock(self):
        for rc in (0,1,2,7):
            result=self.run_cli("stack-status","demo",MOCK_STATUS_RC=str(rc))
            self.assertEqual(result.returncode,rc,result.stderr)
        self.assertFalse((self.instance/".operation.lock").exists())
        self.assertEqual(self.run_cli("stack-status","missing").returncode,2)

    def test_opt_out_common_policy_and_endpoint_pins(self):
        control=self.instance/"control.env"
        control.write_text(control.read_text()+"STACK_MANAGED=no\n")
        before=control.read_bytes()
        result=self.run_cli("apply-stack","demo","-y","--no-restart")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("apply --policy-only",self.calls())
        self.assertEqual(control.read_bytes(),before)
        result=self.run_cli("stack-status","demo",MOCK_STATUS_RC="1")
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn("status --policy-only",self.calls())

    def test_endpoint_failure_is_operational_not_drift(self):
        self.script("curl", "#!/bin/sh\nexit 7\n")
        result=self.run_cli("stack-status", "demo", MOCK_STATUS_RC="1")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((self.instance/".operation.lock").exists())

    def test_supervisor_unit_template_uses_install_path_not_host_path(self):
        unit = (ROOT / "systemd/hermes-fleet-supervisor.service").read_text()
        source = (ROOT / "hermes-spawn.sh").read_text()
        self.assertEqual(unit.count("@HERMES_SPAWNING_REPO@"), 2)
        self.assertNotIn("ExecStart=/root/", unit)
        self.assertIn('sed "s|@HERMES_SPAWNING_REPO@|$SCRIPT_DIR|g"', source)
        rendered = unit.replace("@HERMES_SPAWNING_REPO@", str(self.repo))
        self.assertIn(f"ExecStart={self.repo}/scripts/fleet-supervisor.sh", rendered)
        self.assertNotIn("@HERMES_SPAWNING_REPO@", rendered)

    def test_supervisor_only_heals_drift(self):
        shutil.copy2(ROOT/"scripts/fleet-supervisor.sh",self.repo/"supervisor.sh")
        self.script("sleep", "#!/bin/sh\nexit 97\n")
        # Execute precisely one real loop; avoid waiting or racing a live process.
        p=self.repo/"supervisor.sh"
        p.write_text(p.read_text().replace('  sleep "$INTERVAL"','  exit 0'))
        self.script("docker", '#!/bin/sh\ncase "$*" in *"ps --status"*) echo hermes;; *"serve status"*) echo "9119 /webui 8787 :8788";; esac\n')
        (self.repo/"hermes-spawn.sh").write_text('#!/bin/sh\necho "$*" >> "$MOCK_LOG"\n[ "$1" != stack-status ] || exit "$MOCK_STATUS_RC"\n')
        for rc in (0,1,2,7):
            self.log.write_text("")
            result=subprocess.run(["sh",str(p)],env=dict(self.env, HERMES_SPAWNING_REPO=str(self.repo),MOCK_STATUS_RC=str(rc)),capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual("apply-stack demo -y --restart" in self.calls(),rc==1)
        self.assertIn("operational error (exit 7)",(self.repo/"instances/.supervisor.log").read_text())

    def _secret_skill(self):
        path = self.repo / "skills/fleet-organism-design/SKILL.md"
        return path, self.home / "fleet-skills/fleet-organism-design/SKILL.md"

    def test_install_skills_atomic_whole_tree_and_private_rollback(self):
        src, live = self._secret_skill()
        src.write_text(src.read_text() + "\nseed-v1\n")
        fresh = self.run_cli("install-skills", "demo")
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertEqual(live.read_bytes(), src.read_bytes())
        for source_file in self.repo.joinpath("skills").rglob("*"):
            if source_file.is_file():
                staged_file = self.home / "fleet-skills" / source_file.relative_to(self.repo / "skills")
                self.assertEqual(staged_file.read_bytes(), source_file.read_bytes())
        self.assertEqual(live.stat().st_mode & 0o777, 0o644)
        self.assertEqual(live.parent.parent.stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.home / ".fleet-skills-history").exists())
        self.assertFalse(list(self.instance.glob(".fleet-skills-stage-*")))
        source_bytes = src.read_bytes()
        unchanged = self.run_cli("install-skills", "demo")
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
        self.assertIn("3 unchanged", unchanged.stdout)
        self.assertFalse((self.instance / ".fleet-skills-history").exists())
        self.assertEqual(src.read_bytes(), source_bytes)

        # Local edits inside a managed skill are retired, not blended into the
        # canonical skill. A distinct unmanaged skill is kept in the live tree.
        live.write_text("private-local-edit\n")
        (live.parent / "local-note.txt").write_text("local-only\n")
        custom = self.home / "fleet-skills/user-created/SKILL.md"
        custom.parent.mkdir()
        custom.write_text("user-only\n")
        src.write_text(src.read_text() + "seed-v2\n")
        updated = self.run_cli("install-skills", "demo")
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertEqual(live.read_bytes(), src.read_bytes())
        self.assertFalse((live.parent / "local-note.txt").exists())
        self.assertEqual(custom.read_text(), "user-only\n")
        history = self.instance / ".fleet-skills-history"
        snapshots = list(history.iterdir())
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(history.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.instance.stat().st_mode & 0o777, 0o700)
        previous = snapshots[0] / "fleet-organism-design"
        self.assertEqual((previous / "SKILL.md").read_text(), "private-local-edit\n")
        self.assertEqual((previous / "local-note.txt").read_text(), "local-only\n")
        self.assertEqual((snapshots[0] / "user-created/SKILL.md").read_text(), "user-only\n")
        self.assertFalse((self.home / ".fleet-skills-history").exists())
        self.assertFalse(list(self.instance.glob(".fleet-skills-stage-*")))
        repeat = self.run_cli("install-skills", "demo")
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(len(list(history.iterdir())), 1)

    def test_install_skills_failure_does_not_publish_and_orphan_retry(self):
        src, live = self._secret_skill()
        self.assertEqual(self.run_cli("install-skills", "demo").returncode, 0)
        initial = live.read_bytes()
        src.write_text(src.read_text() + "\nsecret-token-NEVER-PRINT\n")
        bad_copy = self.run_cli("install-skills", "demo", MOCK_CP_FAIL="1")
        self.assertNotEqual(bad_copy.returncode, 0)
        self.assertEqual(live.read_bytes(), initial)
        self.assertNotIn("secret-token-NEVER-PRINT", bad_copy.stdout + bad_copy.stderr)
        orphans = list(self.instance.glob(".fleet-skills-stage-*"))
        self.assertTrue(orphans)
        self.assertEqual([p for p in self.home.glob(".fleet-skills-stage-*")], [])
        self.assertTrue(all(p.is_dir() for p in orphans))
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o700 for p in orphans))
        self.assertEqual(self.instance.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.home / ".fleet-skills-history").exists())
        self.assertFalse((self.instance / ".fleet-skills-history").exists())
        tampered = self.run_cli("install-skills", "demo", MOCK_CP_TAMPER="1")
        self.assertNotEqual(tampered.returncode, 0)
        self.assertEqual(live.read_bytes(), initial)
        self.assertNotIn("CORRUPTED", tampered.stdout + tampered.stderr)
        failed_publish = self.run_cli("install-skills", "demo", MOCK_PUBLISH_FAIL="1")
        self.assertNotEqual(failed_publish.returncode, 0)
        self.assertEqual(live.read_bytes(), initial)
        self.assertGreater(len(list(self.instance.glob(".fleet-skills-stage-*"))), len(orphans))
        retry = self.run_cli("install-skills", "demo")
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(live.read_bytes(), src.read_bytes())
        self.assertTrue(all(p.exists() for p in orphans))
        self.assertTrue((self.instance / ".fleet-skills-history").is_dir())

    def test_install_skills_rejects_symlinks_and_special_source(self):
        outside = self.repo / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text("outside")
        live = self.home / "fleet-skills"
        live.symlink_to(outside, target_is_directory=True)
        attempt = self.run_cli("install-skills", "demo")
        self.assertNotEqual(attempt.returncode, 0)
        self.assertEqual((outside / "SKILL.md").read_text(), "outside")
        live.unlink()
        src = self.repo / "skills/fleet-organism-design"
        (src / "LEAK").symlink_to(self.instance / "control.env")
        attempt = self.run_cli("install-skills", "demo")
        self.assertNotEqual(attempt.returncode, 0)
        self.assertFalse(live.exists())
        self.assertNotIn("fake-key", attempt.stdout + attempt.stderr)

    def test_install_skills_rejects_untrusted_history(self):
        self.assertEqual(self.run_cli("install-skills", "demo").returncode, 0)
        src, live = self._secret_skill()
        original = live.read_bytes()
        src.write_text(src.read_text() + "\nchange\n")
        (self.instance / ".fleet-skills-history").symlink_to(self.repo / "skills")
        result = self.run_cli("install-skills", "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(live.read_bytes(), original)

if __name__ == "__main__":
    unittest.main()
