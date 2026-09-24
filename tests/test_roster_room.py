"""Read-only room registry diagnostics, on synthetic fixtures only."""
import importlib.util
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "skills" /
          "hermes-bot-roster-and-rooms" / "scripts" / "rebuild_room_registry.py")
spec = importlib.util.spec_from_file_location("roster_room_diagnostics", SCRIPT)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def room(name="Desk", room_id=None, log=None, members=None, revision=7):
    return {
        "name": name, "roomId": room_id,
        "members": members if members is not None else [{"name": "scout"}],
        "log": log if log is not None else [{"id": "msg-1", "text": "keep this"}],
        "revision": revision,
    }


class RoomRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "profile.yaml"
        self.original_doc = {
            "name": "default", "ui_meta": {
                "hermes-bots-groups": {
                    "version": 12, "updatedAt": 1790000000000,
                    "rooms": {"name:Desk": room()},
                    "deleted": {"name:Closed": 4},
                },
            }, "_ui_meta_revisions": {"hermes-bots-groups": 42},
        }
        self.write(self.original_doc)

    def write(self, doc):
        self.path.write_text(yaml.safe_dump(doc, sort_keys=False))
        self.before = self.path.read_bytes()

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = script.main(["--profile-yaml", str(self.path), *args])
        self.assertEqual(self.path.read_bytes(), self.before,
                         "even failed diagnostic paths must not alter the file")
        self.assertEqual(sorted(Path(self.tmp.name).iterdir()), [self.path],
                         "no backup/tmp/other files may be written")
        return status, out.getvalue(), err.getvalue()

    def test_native_null_name_room_is_healthy_and_keeps_log_members_and_tombstone(self):
        for args in [(), ("--verify",), ("--verify", "--personas", "scout")]:
            status, out, err = self.run_main(*args)
            self.assertEqual(status, 0, (out, err))
            self.assertIn("name:Desk", out)
            self.assertIn("roomId=None", out)
            self.assertIn("stored_log_entries=1", out)
            self.assertIn("Desktop-native shape", out)
            self.assertIn("not live-client visibility", out)
            self.assertIn("ask the operator", out)
        self.assertEqual(yaml.safe_load(self.path.read_text()), self.original_doc)

    def test_hosted_engine_never_infers_desktop_visibility_or_repairs_null_room(self):
        status, out, err = self.run_main("--engine", '{"engine-room": "Desk"}')
        self.assertEqual(status, 2)
        self.assertEqual(out, "")
        self.assertIn("invisible-to-Desktop", err)
        self.assertEqual(yaml.safe_load(self.path.read_text()), self.original_doc)

    def test_apply_disabled_even_with_engine_personas_verify_and_missing_file(self):
        combos = [
            ("--apply",),
            ("--apply", "--verify"),
            ("--apply", "--engine", '{"engine-room": "Desk"}',
             "--personas", "scout"),
            ("--apply", "--profile-yaml", str(self.path) + "-absent"),
        ]
        for args in combos:
            status, out, err = self.run_main(*args)
            self.assertEqual(status, 2)
            self.assertEqual(out, "")
            self.assertIn("--apply is disabled", err)

    def test_id_room_is_saved_projection_not_proof_hosted_posts_render(self):
        doc = self.original_doc.copy()
        doc["ui_meta"] = {"hermes-bots-groups": {
            "rooms": {"id:engine-room": room(room_id="engine-room", log=[])},
            "deleted": {"name:Closed": 4}, "version": 12,
        }}
        self.write(doc)
        status, out, err = self.run_main("--verify")
        self.assertEqual(status, 1, (out, err))
        self.assertIn("ID-keyed projection", out)
        self.assertIn("hosted-engine posts do not render in Desktop", out)
        self.assertIn("no name:/roomId:null Desktop-native room", out)
        self.assertIn("not live-client visibility", out)

    def test_revision_gated_tombstone_replay_preserves_source(self):
        doc = self.original_doc
        groups = doc["ui_meta"]["hermes-bots-groups"]
        groups["deleted"]["name:Desk"] = 6  # stale: room revision 7 survives
        groups["rooms"]["id:Tombstoned"] = room("Old", "Tombstoned")
        groups["deleted"]["id:Tombstoned"] = 1  # id tombstone final
        self.write(doc)
        status, out, err = self.run_main("--verify")
        self.assertEqual(status, 0, (out, err))
        self.assertIn("name:Desk", out)
        self.assertIn("stale tombstone", out)
        self.assertNotIn("id:Tombstoned", out)
        self.assertEqual(yaml.safe_load(self.path.read_text()), doc)
        groups["deleted"]["name:Desk"] = 7  # final
        self.write(doc)
        status, out, err = self.run_main("--verify")
        self.assertEqual(status, 1)
        self.assertIn("no active rooms", out)
        self.assertEqual(yaml.safe_load(self.path.read_text()), doc)

    def test_missing_projection_and_member_mismatch_are_not_fake_successes(self):
        status, out, err = self.run_main("--personas", "other")
        self.assertEqual(status, 1)
        self.assertIn("differ from expected", out)
        self.write({"ui_meta": {}, "name": "default"})
        status, out, err = self.run_main("--verify")
        self.assertEqual(status, 2)
        self.assertIn("no Desktop room projection", err)

    def test_malformed_registry_fails_closed_without_writes(self):
        doc = self.original_doc
        groups = doc["ui_meta"]["hermes-bots-groups"]
        groups["deleted"]["name:Closed"] = "not-a-revision"
        self.write(doc)
        status, out, err = self.run_main("--verify")
        self.assertEqual(status, 2)
        self.assertIn("nonnegative integer", err)
        groups["deleted"]["name:Closed"] = 4
        groups["rooms"]["name:Desk"]["log"] = None
        self.write(doc)
        status, out, err = self.run_main("--verify")
        self.assertEqual(status, 1)
        self.assertIn("members and log must be lists", out)


class NoDestructiveRoomDocsTests(unittest.TestCase):
    def test_every_shipped_surgery_reference_refuses_engine_conversion(self):
        duplicate = (ROOT / "skills/hermes-group-chat-delivery/references/room-registry-surgery.md").read_text()
        canonical = (ROOT / "skills/hermes-bot-roster-and-rooms/references/room-registry-surgery.md").read_text()
        for doc in (duplicate, canonical):
            self.assertIn("roomId: null", doc)
            self.assertNotIn("Create `rooms['id:<engineRoomId>']`", doc)
        self.assertIn("Do not use that recipe", duplicate)

    def test_desktop_reference_keeps_readback_separate_from_visibility(self):
        reference = (ROOT / "skills/hermes-group-chat-delivery/references/desktop-ui-meta-delivery.md").read_text()
        self.assertIn("`busy: false` alone", reference)
        self.assertIn("not guarantee", reference)
        self.assertIn("**not** automatically a CAS conflict", reference)
        self.assertNotIn("That alone makes the text appear", reference)


if __name__ == "__main__":
    unittest.main()
