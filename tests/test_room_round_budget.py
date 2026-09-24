"""Offline synthetic Desktop room round checks; no gateway, rooms or model calls."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
MODULE = Path(__file__).resolve().parents[1] / "skills/hermes-group-chat-delivery/references/desktop_group_post.py"
_spec = importlib.util.spec_from_file_location("desktop_group_post_offline", MODULE)
room = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(room)


class Clock:
    def __init__(self):
        self.at = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.at

    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.at += seconds


class FakeRPC:
    def __init__(self, clock, responses=None):
        self.clock = clock
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.calls = []
        self.revision = 1
        self.snapshot = {"rooms": {"name:Room": {"name": "Room", "roomId": None,
                                                 "revision": 1, "log": []}}, "updatedAt": 0}

    def __call__(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "profiles.list":
            return {"profiles": [{"name": "default", "ui_meta": {room.META_KEY: copy.deepcopy(self.snapshot)},
                                  "ui_meta_revisions": {room.META_KEY: self.revision}}]}
        if method == "profiles.configure":
            if params["ui_meta_expected_revisions"][room.META_KEY] != self.revision:
                return {"applied": {"ui_meta": False}, "secret": "token-private"}
            self.snapshot = copy.deepcopy(params["ui_meta"][room.META_KEY])
            self.revision += 1
            return {"applied": {"ui_meta": True}}
        if method == "session.resume":
            profile = params["profile"]
            events = self.responses.get(profile, [])
            if events:
                return copy.deepcopy(events.pop(0))
            return {"messages": [], "busy": False}
        if method == "prompt.submit":
            return {"ok": True}
        raise AssertionError(method)


class RoomRoundTests(unittest.TestCase):
    def test_current_client_thread_id_shape(self):
        self.assertRegex(room.mint_thread_id(), r"^t[0-9a-z]+-[0-9a-f]{5}$")

    def setUp(self):
        self.clock = Clock()
        self.clock_patches = (patch.object(room.time, "monotonic", self.clock.monotonic),
                              patch.object(room.time, "sleep", self.clock.sleep))
        for p in self.clock_patches:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def members(count=3):
        return [{"name": "bot" + str(i)} for i in range(count)]

    def fake_session(self, rpc, member, room_id, thread):
        return member["name"] + "-runtime", member["name"] + "-stored"

    def test_compat_default_is_420_times_roster_and_recomputes_slices(self):
        rpc = FakeRPC(self.clock)
        deadlines = []
        # First participant uses their full 420s, the next replies after 10s;
        # member three gets the freed 410s under the SAME round deadline.
        def turn(_rpc, member, title, submit_id, prompt, deadline):
            deadlines.append((member["name"], deadline, self.clock.at))
            if member["name"] == "bot0":
                self.clock.at = deadline
                return "(pass)"
            if member["name"] == "bot1":
                self.clock.at += 10
                return "(pass)"
            return "Decision from the tail"
        with patch.object(room, "ensure_member_session", self.fake_session), \
             patch.object(room, "run_member_turn_by_title", turn):
            entries = room.drive_round(rpc, "name:Room", self.members(), "tm1", ["You (user): hello"])
        self.assertEqual([int(d[1]-d[2]) for d in deadlines], [420, 420, 830])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["text"], "Decision from the tail")
        self.assertEqual(rpc.snapshot["rooms"]["name:Room"]["log"][0]["id"], entries[0]["id"])
        self.assertLess(self.clock.at, 1360)  # total deadline 100 + 3 * 420

    def test_explicit_shared_budget_floor_and_timeout_is_not_pass(self):
        rpc = FakeRPC(self.clock)
        deadlines = []
        def turn(_rpc, member, title, submit_id, prompt, deadline):
            deadlines.append((member["name"], deadline))
            if member["name"] == "bot0":
                self.clock.at = deadline
                return None  # slow turn never produced observable reply
            if member["name"] == "bot1":
                return "(pass)"  # only this observed literal counts as pass
            return "Lead ruling"
        with patch.object(room, "ensure_member_session", self.fake_session), \
             patch.object(room, "run_member_turn_by_title", turn):
            with self.assertRaises(room.RoundIncomplete) as caught:
                room.drive_round(rpc, "name:Room", self.members(), "tm1", [],
                                 round_budget_s=30, min_member_budget_s=5)
        self.assertEqual([int(x[1]) for x in deadlines], [110, 120, 130])
        self.assertEqual(caught.exception.unfinished, ("bot0",))
        self.assertEqual([e["text"] for e in caught.exception.appended], ["Lead ruling"])
        self.assertEqual(len(rpc.snapshot["rooms"]["name:Room"]["log"]), 1)

    def test_tail_not_submitted_after_deadline_is_explicitly_unfinished(self):
        rpc = FakeRPC(self.clock)
        seen = []
        def turn(_rpc, member, title, submit_id, prompt, deadline):
            seen.append(member["name"])
            # An over-budget synchronous RPC cannot be interrupted by the
            # cooperative deadline; it must have a transport timeout too.
            self.clock.at = deadline + 7 if member["name"] == "bot1" else deadline
            return None
        with patch.object(room, "ensure_member_session", self.fake_session), \
             patch.object(room, "run_member_turn_by_title", turn):
            with self.assertRaises(room.RoundIncomplete) as caught:
                room.drive_round(rpc, "name:Room", self.members(), "tm1", [],
                                 per_member_budget_s=30, round_budget_s=6,
                                 min_member_budget_s=5)
        self.assertEqual(seen, ["bot0", "bot1"])
        self.assertEqual(caught.exception.unfinished, ("bot0", "bot1", "bot2"))
        self.assertEqual(caught.exception.appended, [])

    def test_busy_false_tools_and_empty_do_not_end_turn_until_real_text(self):
        rpc = FakeRPC(self.clock, {"bot0": [
            {"messages": [], "busy": False},  # baseline
            {"messages": [{"role": "tool", "text": "working"}], "busy": False},
            {"messages": [{"role": "tool", "text": "working"},
                          {"role": "assistant", "text": ""}], "busy": False},
            {"messages": [{"role": "tool", "text": "working"},
                          {"role": "assistant", "text": "Verified ruling"}], "busy": False},
        ]})
        deadline = self.clock.at + 13
        text = room.run_member_turn_by_title(rpc, {"name": "bot0"}, "Group: Room · tm1",
                                             "runtime0", "prompt", deadline)
        self.assertEqual(text, "Verified ruling")
        self.assertEqual(self.clock.sleeps, [5, 5, 3])
        self.assertEqual([p["session_id"] for m, p in rpc.calls if m == "session.resume"],
                         ["Group: Room · tm1"] * 4)
        self.assertEqual([p["session_id"] for m, p in rpc.calls if m == "prompt.submit"],
                         ["runtime0"])

    def test_short_slice_reads_at_boundary_and_returns_none_if_no_answer(self):
        rpc = FakeRPC(self.clock, {"bot0": [
            {"messages": [], "busy": False},
            {"messages": [{"role": "assistant", "text": "(pass)"}], "busy": False},
        ]})
        result = room.run_member_turn_by_title(rpc, {"name": "bot0"}, "title", "runtime0",
                                               "prompt", self.clock.at + 2)
        self.assertEqual(result, "(pass)")
        self.assertEqual(self.clock.sleeps, [2])
        rpc.responses["bot0"] = [{"messages": [], "busy": False},
                                 {"messages": [], "busy": False}]
        result = room.run_member_turn(rpc, {"name": "bot0"}, "runtime0", "stored0",
                                      "prompt", self.clock.at + 1)
        self.assertIsNone(result)
        self.assertEqual(self.clock.sleeps, [2, 1])

    def test_round_shorter_than_poll_tick_still_has_bounded_slice(self):
        rpc = FakeRPC(self.clock)
        seen = []
        def turn(_rpc, member, title, submit_id, prompt, deadline):
            seen.append(deadline)
            self.clock.at = deadline
            return "(pass)"
        with patch.object(room, "ensure_member_session", self.fake_session), \
             patch.object(room, "run_member_turn_by_title", turn):
            entries = room.drive_round(rpc, "name:Room", self.members(2),
                                       "tm1", [], round_budget_s=2)
        self.assertEqual(seen, [101, 102])
        self.assertEqual(entries, [])  # two observed passes, no timeout

    def test_rejected_ui_meta_never_echoes_secret_or_retries(self):
        rpc = FakeRPC(self.clock)
        def reject(method, params):
            rpc.calls.append((method, copy.deepcopy(params)))
            return {"ok": False, "applied": {"ui_meta": False},
                    "credentials": "SENSITIVE-DO-NOT-LOG"}
        with self.assertRaises(RuntimeError) as caught:
            room.write_projection(reject, {"rooms": {}}, expected_revision=1)
        self.assertNotIn("SENSITIVE-DO-NOT-LOG", str(caught.exception))
        self.assertIn("cause unknown", str(caught.exception))
        self.assertEqual([m for m, _ in rpc.calls], ["profiles.configure"])

    def test_bad_budget_rejected_before_network(self):
        rpc = FakeRPC(self.clock)
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                room.drive_round(rpc, "name:Room", self.members(), "tm1", [],
                                 round_budget_s=value)
        self.assertEqual(rpc.calls, [])


if __name__ == "__main__":
    unittest.main()
