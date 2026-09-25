"""The fleet reset is exact, stopped-only, and recoverable."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/reset-fleet-cognition.py"
SPEC = importlib.util.spec_from_file_location("reset_fleet_cognition", SCRIPT)
reset = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(reset)


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for skill in ("fleet-organism-design", "fleet-convergence-learning",
                      "hermes-group-chat-delivery"):
            path = self.repo / "skills" / skill / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"# {skill}\n")
        seed = self.repo / "skills/fleet-organism-design/references/fleet-seed.md"
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text(f"before\n{reset.SOUL_START}\nFRACTAL OODA\n"
                        f"## PURPOSEFUL CONFIDENCE\ncore\n{reset.SOUL_END}\nafter\n")
        self.instance = self.repo / "instances/demo"
        home = self.instance / "hermes-data"
        (home / "profiles/scout").mkdir(parents=True)
        (self.instance / "control.env").write_text(
            "COMPOSE_PROJECT_NAME=hermes-demo\nHERMES_UID=10000\nHERMES_GID=10000\n")
        for profile in (home, home / "profiles/scout"):
            (profile / "config.yaml").write_text("secret: preserved\n")
            (profile / "profile.yaml").write_text("ui_meta: {preserved: true}\n")
            (profile / "SOUL.md").write_text("old brain\n")
            (profile / "memories").mkdir()
            (profile / "memories/old.md").write_text("old memory\n")
            (profile / "cron").mkdir()
            (profile / "cron/jobs.json").write_text('{"jobs":[{"id":"old"}]}')
            (profile / "state.db").write_bytes(b"old conversations")
            (profile / "skills/old").mkdir(parents=True)
            (profile / "skills/old/SKILL.md").write_text("old tool\n")
        (home / "fleet-skills/old").mkdir(parents=True)
        (home / "fleet-skills/old/SKILL.md").write_text("old shared\n")
        (home / "kanban/boards/old/workspaces/card").mkdir(parents=True)
        (home / "kanban/boards/old/workspaces/card/report.md").write_text("paperwork\n")
        (home / "mission-source").mkdir()
        (home / "mission-source/source.json").write_text('{"truth":true}')
        (home / ".env").write_text("SECRET=preserved\n")
        (home / "tao-fleet/desk-six/cycles/cycle-1/lane").mkdir(parents=True)
        (home / "tao-fleet/desk-six/cycles/cycle-1/lane/retro.md").write_text("old\n")
        (self.instance / "formation-admission.json").write_text("old admission")

    @patch.object(reset, "_running_containers", return_value=[])
    def test_dry_run_changes_nothing(self, _running):
        before = {str(path.relative_to(self.instance)): path.read_bytes()
                  for path in self.instance.rglob("*") if path.is_file()}
        backup = self.root / "backup"
        report = reset.execute([self.instance], repo=self.repo, backup=backup, apply=False)
        self.assertFalse(report["applied"])
        self.assertFalse(backup.exists())
        after = {str(path.relative_to(self.instance)): path.read_bytes()
                 for path in self.instance.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    @patch.object(reset, "_running_containers", return_value=[])
    def test_apply_moves_cognition_preserves_sources_and_bootstraps(self, _running):
        backup = self.root / "backup"
        report = reset.execute([self.instance], repo=self.repo, backup=backup, apply=True)
        self.assertTrue(report["applied"])
        home = self.instance / "hermes-data"
        self.assertEqual((home / ".env").read_text(), "SECRET=preserved\n")
        self.assertEqual((home / "config.yaml").read_text(), "secret: preserved\n")
        self.assertIn("preserved", (home / "profile.yaml").read_text())
        self.assertTrue((home / "mission-source/source.json").is_file())
        self.assertIn("FORMING", (home / "SOUL.md").read_text())
        self.assertIn("PURPOSEFUL CONFIDENCE", (home / "SOUL.md").read_text())
        self.assertEqual(json.loads((home / "cron/jobs.json").read_text()), {"jobs": []})
        self.assertTrue((home / "fleet-skills/fleet-organism-design/SKILL.md").is_file())
        self.assertTrue((home / "fleet-skills/fleet-convergence-learning/SKILL.md").is_file())
        self.assertFalse((home / "kanban").exists())
        self.assertFalse((home / "tao-fleet/desk-six/cycles").exists())
        self.assertTrue((backup / "demo/hermes-data/tao-fleet/desk-six/cycles/cycle-1/lane/retro.md").is_file())
        self.assertTrue((backup / "demo/hermes-data/state.db").is_file())
        self.assertTrue((backup / "demo/formation-admission.json").is_file())
        self.assertTrue((backup / "reset-ledger.json").is_file())


    @patch.object(reset, "_running_containers", return_value=[])
    def test_bootstrap_chowns_created_files_to_container_owner(self, _running):
        backup = self.root / "backup2"
        with patch.object(reset, "_chown_container", wraps=reset._chown_container) as chown:
            reset.execute([self.instance], repo=self.repo, backup=backup, apply=True)
        home = self.instance / "hermes-data"
        created = {(home / "SOUL.md"), (home / "cron"), (home / "cron/jobs.json"),
                   (home / "profiles/scout/SOUL.md"), (home / "profiles/scout/cron"),
                   (home / "profiles/scout/cron/jobs.json"),
                   (home / "fleet-skills"),
                   (home / "fleet-skills/fleet-organism-design/SKILL.md")}
        chowned = {c.args[0] for c in chown.call_args_list}
        self.assertTrue(created.issubset(chowned), created - chowned)
        for c in chown.call_args_list:
            self.assertEqual(c.args[1], (10000, 10000))
        if hasattr(Path, "owner") and chown.call_args_list:
            st = (home / "SOUL.md").stat()
            if chown.call_args_list and st.st_uid != 0:
                self.assertEqual(st.st_uid, 10000)

    @patch.object(reset, "_running_containers", return_value=["abc hermes-demo-hermes-1"])
    def test_running_instance_is_refused_before_backup(self, _running):
        backup = self.root / "backup"
        with self.assertRaises(reset.ResetError):
            reset.execute([self.instance], repo=self.repo, backup=backup, apply=True)
        self.assertFalse(backup.exists())
        self.assertEqual((self.instance / "hermes-data/SOUL.md").read_text(), "old brain\n")


if __name__ == "__main__":
    unittest.main()
