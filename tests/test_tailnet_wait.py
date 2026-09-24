"""Synthetic shell regressions; no Docker access or fleet changes."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATTERN = '\'"BackendState"[[:space:]]*:[[:space:]]*"Running"\''


class TailnetWaitTests(unittest.TestCase):
    def run_shell(self, command, payload):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(json.dumps(payload, indent=2))
            return subprocess.run(
                ["bash", "-c", 'set -euo pipefail; state="$(cat "$1")"; ' + command,
                 "test", str(path)], capture_output=True, text=True, timeout=10,
            )

    def test_old_pipeline_sigpipe_on_large_valid_status(self):
        payload = {"BackendState": "Running", "Peer": {"synthetic": {"padding": "x" * 1048576}}}
        result = self.run_shell('printf \'%s\\n\' "$state" | grep -Eq '  + PATTERN, payload)
        self.assertEqual(result.returncode, 141, result.stderr)

    def test_production_predicate_handles_large_status_and_rejects_login(self):
        source = (ROOT / "hermes-spawn.sh").read_text()
        body = source.split("wait_for_tailscale() {", 1)[1].split("\n}", 1)[0]
        line = next(line.strip() for line in body.splitlines() if line.strip().startswith("if "))
        predicate = line.removeprefix("if ").removesuffix("; then")
        self.assertIn('<<< "$state"', predicate)
        for state in ("Running", "NeedsLogin", "Stopped"):
            with self.subTest(state=state):
                payload = {"BackendState": state, "Peer": {"synthetic": {"padding": "x" * 1048576}}}
                result = self.run_shell(
                    "if " + predicate + "; then printf ready; else printf waiting; fi", payload
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "ready" if state == "Running" else "waiting")


if __name__ == "__main__":
    unittest.main()
