"""Canonical SOUL core sync preserves persona-specific identity."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import sync_soul_core as syncer


class SoulSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.instance = self.root / "instance"
        home = self.instance / "hermes-data"
        (home / "profiles/scout").mkdir(parents=True)
        for profile in (home, home / "profiles/scout"):
            (profile / "config.yaml").write_text("{}\n")
            (profile / "SOUL.md").write_text(f"# {profile.name} identity\nOwn the role.\n")
        self.seed = self.root / "seed.md"
        self.core = (f"{syncer.START}\nFRACTAL OODA\n## PURPOSEFUL CONFIDENCE\n"
                     "Failure is a steering signal, never identity.\n"
                     f"{syncer.END}")
        self.seed.write_text("before\n" + self.core + "\nafter\n")

    def test_check_then_apply_every_profile_without_erasing_identity(self):
        changes, rc = syncer.sync(self.instance, self.seed, apply=False)
        self.assertEqual(rc, 1)
        self.assertEqual([item["changed"] for item in changes], [True, True])
        changes, rc = syncer.sync(self.instance, self.seed, apply=True)
        self.assertEqual(rc, 0)
        for profile in (self.instance / "hermes-data",
                        self.instance / "hermes-data/profiles/scout"):
            value = (profile / "SOUL.md").read_text()
            self.assertEqual(value.count(syncer.START), 1)
            self.assertIn("Own the role.", value)
        changes, rc = syncer.sync(self.instance, self.seed, apply=False)
        self.assertEqual(rc, 0)
        self.assertFalse(any(item["changed"] for item in changes))

    def test_canonical_update_replaces_only_marked_block(self):
        syncer.sync(self.instance, self.seed, apply=True)
        self.seed.write_text("before\n" + self.core.replace("steering", "course-correction") + "\nafter\n")
        syncer.sync(self.instance, self.seed, apply=True)
        value = (self.instance / "hermes-data/SOUL.md").read_text()
        self.assertIn("course-correction", value)
        self.assertNotIn("steering signal", value)
        self.assertIn("Own the role.", value)

    def test_malformed_markers_fail_without_write(self):
        soul = self.instance / "hermes-data/SOUL.md"
        soul.write_text(syncer.START + "\nbroken\n")
        before = soul.read_bytes()
        with self.assertRaises(syncer.SyncError):
            syncer.sync(self.instance, self.seed, apply=True)
        self.assertEqual(soul.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
