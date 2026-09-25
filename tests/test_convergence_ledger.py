"""Deterministic tests for the sparse convergence ledger."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
SCRIPT_DIR = (Path(__file__).resolve().parents[1] / "skills" /
              "fleet-convergence-learning" / "scripts")
sys.path.insert(0, str(SCRIPT_DIR))
import convergence


class ConvergenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "state" / "convergence.db"

    def call(self, *args: str, want: int = 0) -> dict:
        output = io.StringIO()
        with redirect_stdout(output):
            rc = convergence.main(["--db", str(self.db), *args])
        self.assertEqual(rc, want, output.getvalue())
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        return json.loads(lines[0])

    def record(self, *, actor: str = "scout", outcome: str = "failure",
               approach: str = "reuse guessed connection id",
               conditions: str = '{"client":"desktop","gateway":"0.21.4"}',
               signature: str = "desktop-room-seat:unverified-connection-id") -> dict:
        args = ["record", "--goal", "qualify rooms", "--scope", "formation",
                "--actor", actor, "--approach", approach, "--outcome", outcome,
                "--summary", "Desktop filter did not expose the room",
                "--conditions", conditions, "--rewards", '{"visible":0,"cost":-1}',
                "--versions", '{"hermes":"0.21.4"}',
                "--source", "room:name:Coordination/event:post-17"]
        if outcome == "failure":
            args.extend(["--failure-signature", signature])
        return self.call(*args)

    def test_record_is_sparse_append_only_and_exact_duplicates_collapse(self):
        first = self.record()
        second = self.record()
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(first["episode_id"], second["episode_id"])
        stats = self.call("stats")
        self.assertEqual(stats["episodes"], 1)
        self.assertEqual(stats["outcomes"], {"failure": 1})
        with sqlite3.connect(self.db) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE episodes SET summary='rewritten' WHERE id=?",
                           (first["episode_id"],))

    def test_retry_guard_denies_same_failure_but_allows_changed_conditions(self):
        episode = self.record()
        denied = self.call("guard", "--goal", "qualify rooms", "--scope", "formation",
                           "--failure-signature",
                           "desktop-room-seat:unverified-connection-id",
                           "--conditions", '{"client":"desktop","gateway":"0.21.4"}',
                           want=3)
        self.assertFalse(denied["allowed"])
        self.assertEqual(denied["matching_episode_ids"], [episode["episode_id"]])
        allowed = self.call("guard", "--goal", "qualify rooms", "--scope", "formation",
                            "--failure-signature",
                            "desktop-room-seat:unverified-connection-id",
                            "--conditions", '{"client":"desktop","gateway":"0.21.5"}')
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["reason"], "conditions_changed")

    def test_pattern_needs_independent_curator_and_recall_is_bounded(self):
        episode = self.record()
        base = ["propose-pattern", "--goal", "qualify rooms", "--scope", "formation",
                "--kind", "avoid", "--conditions", '{"client":"desktop"}',
                "--guidance", "Use only the operator-verified Desktop connection id.",
                "--confidence", "0.9", "--versions", '{"hermes":"0.21.4"}',
                "--support", episode["episode_id"]]
        error = self.call(*base, "--curator", "scout", want=2)
        self.assertIn("independent", error["error"])
        pattern = self.call(*base, "--curator", "steward")
        self.assertEqual(pattern["authority"], "advisory")
        recalled = self.call("recall", "--goal", "qualify rooms", "--scope", "formation",
                             "--conditions", '{"client":"desktop","gateway":"0.21.4"}',
                             "--query", "connection id", "--limit", "1",
                             "--char-budget", "1200")
        self.assertEqual(recalled["returned"], 1)
        self.assertLessEqual(recalled["characters"], 1200)
        self.assertEqual(recalled["patterns"][0]["authority"], "advisory")
        self.assertEqual(recalled["patterns"][0]["supporting_episode_ids"],
                         [episode["episode_id"]])

    def test_invalid_sources_multiline_reports_and_failure_without_signature_rejected(self):
        common = ["record", "--goal", "g", "--scope", "s", "--actor", "a",
                  "--approach", "x", "--outcome", "failure", "--conditions", "{}",
                  "--summary", "line one\nline two"]
        self.assertIn("one line", self.call(*common, "--source", "tool:item", want=2)["error"])
        common[-1] = "one line"
        self.assertIn("scheme:locator", self.call(*common, "--source", "copied-file", want=2)["error"])
        self.assertIn("failure-signature",
                      self.call(*common, "--source", "tool:item", want=2)["error"])

    def test_compare_preserves_observed_rewards_without_declaring_winner(self):
        one = self.record(outcome="success", actor="builder", approach="use live registry")
        self.assertIn("episode_id", one)
        result = self.call("compare", "--goal", "qualify rooms", "--scope", "formation")
        self.assertEqual(len(result["episodes"]), 1)
        self.assertEqual(result["reward_dimensions"], ["cost", "visible"])
        self.assertIn("no winner is inferred", result["notice"])


if __name__ == "__main__":
    unittest.main()
