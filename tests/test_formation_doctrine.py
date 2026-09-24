"""Doctrine regression fixtures only; these do NOT enforce a live admission gate.

No live instance, model, credential, or scheduler is touched. Actual admission
requires a separately tested fail-closed guard on every execution path.
"""
from pathlib import Path
import hashlib
import unittest

ROOT = Path(__file__).resolve().parents[1] / "skills/fleet-organism-design"
SEED = ROOT / "references/fleet-seed.md"
SKILL = ROOT / "SKILL.md"
TRADING = ROOT / "references/trading-example.md"


class FormationDoctrineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = SEED.read_text(encoding="utf-8")
        cls.skill = SKILL.read_text(encoding="utf-8")

    def test_protected_founding_source_and_handshake(self):
        original_goal = (
            "Build a results-driven six-persona fleet that is operationally "
            "self-aware and self-optimizing through collective GRPO-centered "
            "learning, functioning together as one living organism with distinct "
            "responsibilities and shared outcomes."
        )
        original_role = (
            "You are the first persona and founding steward—one of the six, "
            "not an extra layer. Do not assume the mission domain or invent the "
            "roster before studying the handoff. Build the six responsibilities "
            "backward from the mission."
        )
        self.assertEqual(self.seed.splitlines()[0], "/goal")
        self.assertIn(original_goal, self.seed)
        self.assertIn(original_role, self.seed)
        self.assertTrue(self.seed.rstrip().endswith("I'm ready."))
        self.assertIn("FOR THIS FIRST MESSAGE ONLY", self.seed)
        self.assertIn("the seed wins", self.skill)

    def test_stage_preconditions_and_non_self_approval(self):
        for document in (self.seed, self.skill):
            with self.subTest(document=document[:50]):
                for stage in ("DISCOVERY", "FORMING", "FORMATION_PASSED"):
                    self.assertIn(stage, document)
                self.assertIn("independent", document.lower())
                self.assertIn("operator", document.lower())
                self.assertIn("formation", document.lower())
        self.assertIn("only the operator or an operator-designated acceptance authority", self.seed)
        self.assertIn("only the operator or the acceptance authority designated", self.skill)
        self.assertIn("Passing formation admits\nmission buildout, not production release.", self.skill)
        self.assertIn("not production release", self.seed)
        self.assertIn("preflight", self.seed.lower())
        self.assertIn("Keep schedules\npaused until preflight passes", self.skill)

    def test_negative_cases_are_explicit_in_both_docs(self):
        # Scenario -> bounded text around a rule, not just a loose word.
        cases = {
            "profiles_and_boards_but_room_blocked": (
                "Profiles and boards existing while rooms, schedules, memory, access, or continuation are blocked means **still FORMING**",
                "A blocked\nroom, worker, or continuation path leaves the crew FORMING"),
            "scout_read_only_domain_refresh": (
                'domain analysis, experiments, datasets, algorithm/scorer work, model tests, mission scripts/tools, or publication and connector deployment for mission output, even if framed as "read-only,"',
                'Do not dispatch role owners to domain research or build datasets,\nscorers, models, tests, scripts, tools, or research publications; labels such as\n"read-only"'),
            "researcher_preparatory_proposal": (
                '"preparatory," cheap, or unaffected work',
                '"read-only", "preparatory", or "unaffected" do not exempt mission work'),
            "curator_dataset_metadata": (
                "FORMING` permits crew formation and its bounded capability tests—not domain analysis, experiments, datasets",
                "During FORMING, qualify native tools,\nroles, identity, memory, connectors, boards, rooms, schedules, stops, and\ncontinuation. Do not dispatch role owners to domain research or build datasets"),
            "mission_scoring_and_tools": (
                "algorithm/scorer work, model tests, mission scripts/tools",
                "scorers, models, tests, scripts, tools, or research publications"),
            "publication_before_crew": (
                "using it to publish research is mission work",
                "publication through it is\nmission work"),
            "founder_direct_one_shot_bypass": (
                "native board dispatch, cron, direct/founder and one-shot tools",
                "including native Kanban, cron, direct\nfounder and one-shot execution"),
        }
        for name, (seed_witness, skill_witness) in cases.items():
            with self.subTest(name=name):
                self.assertIn(seed_witness, self.seed)
                self.assertIn(skill_witness, self.skill)
        self.assertIn("Connector authentication for a role is formation", self.skill)
        self.assertIn("A connector authentication probe needed to make a persona operational is formation", self.seed)
        self.assertIn("An explicit operator exception must name its exact scope", self.seed)
        self.assertIn("never switch tracks to mission work", self.skill)

    def test_observation_not_configuration_and_client_visibility(self):
        for document in (self.seed, self.skill):
            with self.subTest(document=document[:50]):
                self.assertIn("conditional_send", document)
                self.assertIn("groups.send", document)
                self.assertIn("ui_meta", document)
                self.assertIn("backend readback", document.lower())
                self.assertIn("handoff", document)
                self.assertIn("file-first", document)
                self.assertIn("stop", document)
                self.assertIn("continuation", document)
                self.assertIn("prose", document)
        self.assertIn("each persona performs its own role", self.seed)
        self.assertIn("each persona participating in\nits role", self.skill)
        self.assertIn("all mission admission/action paths", self.skill)
        self.assertIn("operator-designated acceptance authority", self.seed)

    def test_noop_checks_and_optional_trading_baseline(self):
        for document in (self.seed, self.skill):
            for phrase in ("readback", "stale", "unknown", "remaining", "(pass)"):
                with self.subTest(document=document[:50], phrase=phrase):
                    self.assertIn(phrase, document)
        self.assertNotIn("## Funding certification (the double-spend gap)", self.skill)
        self.assertIn("references/trading-example.md", self.skill)
        trade = TRADING.read_text(encoding="utf-8")
        self.assertIn("optional", trade.splitlines()[0].lower())
        self.assertIn("not** required components", trade)
        self.assertIn("## Funding certification (the double-spend gap)", trade)
        preserved = trade[trade.index("## Arming a desk for live execution") :]
        # Pin the optional trade appendix after genericizing the venue example;
        # changes here require deliberate review, not silent mission drift.
        self.assertEqual(hashlib.sha256(preserved.encode()).hexdigest(),
                         "8068ecbd297582a2b44697ea711ed67cc51ec4323944762caeb8492c7bd09aec")
        self.assertRegex(self.skill, r"(?m)^version: 1\.3\.0$")


if __name__ == "__main__":
    unittest.main()
