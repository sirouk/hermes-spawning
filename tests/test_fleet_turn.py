"""Unit tests for scripts/fleet-turn.py pure helpers (no docker, no containers)."""
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "fleet_turn", Path(__file__).resolve().parent.parent / "scripts" / "fleet-turn.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class TestBusyBanner:
    def test_detects_lock_banner(self):
        assert mod.busy_banner("Another Hermes process is using this session; waiting...")

    def test_detects_refusal_banner(self):
        assert mod.busy_banner("Another Hermes process kept this session busy too long.")

    def test_clean_output_not_busy(self):
        assert not mod.busy_banner("Query: hello\nInitializing agent...\nI'm ready.")

    def test_case_insensitive(self):
        assert mod.busy_banner("another hermes process is using this session")


class TestParseChatPids:
    def test_parses_chat_processes(self):
        ps = (
            "  PID COMMAND\n"
            "    1 python -m hermes_app\n"
            "  437 hermes chat --resume abc --in /opt/data\n"
            "  512 hermes gateway --in /opt/data\n"
            "  613 hermes chat --in /opt/data/profiles/scout\n"
        )
        assert mod.parse_chat_pids(ps) == [437, 613]

    def test_ignores_non_numeric_and_non_chat(self):
        ps = "foo hermes chat\n  99 hermes chat arg\n"
        assert mod.parse_chat_pids(ps) == [99]

    def test_empty(self):
        assert mod.parse_chat_pids("") == []


class TestKillPids:
    def test_no_pids_is_noop(self):
        mod.kill_pids("some-container", [])  # must not call docker at all
