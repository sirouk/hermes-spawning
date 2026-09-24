#!/usr/bin/env python3
"""Profile-local cron wrapper pattern for IN-PROCESS native room callers.

WHY THIS FILE EXISTS

A room cycle that a human fires by hand is not a schedule. This wrapper is
the missing scheduled caller: the exact file a native script-only cron job
runs, so the room cycle fires into the Group Chat without a human.

WHY IT IMPORTS INSTEAD OF SHELLING OUT (the one rule that must not break)

The native identity check reads os.getppid() plus that PID's /proc field 22
(process start time) and requires exactly ONE 'running' execution row in the
profile ledger that is source='builtin', handoff_pending=0, matching both
pid and start time -- crosschecked against HERMES_CRON_SCHEDULED_AT and
HERMES_HOME. Any layer that reparents (a bash -c line, or a subprocess
spawned from here) voids the fingerprint, and the occurrence is correctly
refused as non-native. So:

  - the cron job must point DIRECTLY at this file (no shell wrapper), as a
    no_agent/script_required job with workdir = the module directory;
  - this file must IMPORT the cycle module (module dir on sys.path below),
    never spawn it;
  - it must be installed as a REAL file under profiles/<profile>/scripts/,
    never a symlink.

SENDING IS OFF UNTIL THE GATE OPENS

The wrapper reads <PREFIX>_APPLY from the environment and defaults to
read-only, so installing it on the grid is safe day one. Independently, the
release/gate JSON is re-read inside the occurrence runner before EVERY send
(revocation between checks takes effect), so posts start only when the
operator flips both -- with no edit to this file.

IDEMPOTENCE / RETRY

The occurrence runner seals each occurrence in a SQLite journal keyed by
job_id:scheduled_instant, holds an exclusive flock, reuses the identical
request on retry, and keeps a pre-send in-flight marker. Event IDs derive
from room+job+scheduled_instant+phase, so a retry never allocates a new ID.
A terminal row short-circuits and replays its result. A crash after a send
fences to BLOCKED (human recovery), never a blind resend.

EXIT CODES
  READY/COMPLETE=0, PENDING=2, BLOCKED=3, EXPIRED=4, INDETERMINATE=5, error=1
PENDING is normal and expected: one invocation cannot complete an
asynchronous cycle -- the debate settles first, then the decree lands in
the same thread on a later occurrence. Do not schedule recursive polling.
"""
import json
import os
import sys
from pathlib import Path

# ---- per-deployment constants (edit these when you copy the file) ---------
MODULE_DIR = '/opt/data/<owner>/<cycle-module-dir>'      # holds room_schedule.py / six_context equivalents
MANIFEST = MODULE_DIR + '/manifest.json'
RELEASE = MODULE_DIR + '/room-release.json'              # gate: frontend_verified + scheduled_release_approved + room_id
JOURNAL = '/opt/data/profiles/<profile>/cron/room_cycle_occurrences.db'
APPLY_ENV = 'ROOM_CYCLE_APPLY'                           # prefix per deployment
TRANSPORT_MODULE = '/opt/data/<owner>/room-integration/native_room.py'

EXIT_CODES = {
    'READY': 0,
    'COMPLETE': 0,
    'PENDING': 2,
    'BLOCKED': 3,
    'EXPIRED': 4,
    'INDETERMINATE': 5,
}


def _apply_enabled():
    """Opt-in only. The release gate is still re-checked inside the runner."""
    return os.environ.get(APPLY_ENV, '').strip().lower() in ('1', 'true', 'yes', 'on')


def main():
    # ModuleNotFoundError without this: the cycle module's imports are
    # relative to its own directory.
    if MODULE_DIR not in sys.path:
        sys.path.insert(0, MODULE_DIR)

    from room_schedule import run_occurrence

    def transport():
        """Reuse the existing credential-safe adapter; never re-implement auth.

        The installed pattern: authenticate to the loopback gateway
        (127.0.0.1:9119) with existing dashboard credentials, fetch a WS
        ticket, and pass it as a subprotocol (hermes-gateway-ticket.<ticket>)
        so it never appears in URL or access logs. trust_env=False, identity
        readback, never log credentials.
        """
        import importlib.util
        path = Path(TRANSPORT_MODULE)
        spec = importlib.util.spec_from_file_location('native_room_adapter', path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.NativeRoom()

    apply_send = _apply_enabled()
    try:
        with transport() as rpc:
            result = run_occurrence(MANIFEST, JOURNAL, RELEASE, rpc, apply=apply_send)
    except Exception as exc:
        # Never leak a stack trace into the room or the cron transcript; the
        # contract says retry the IDENTICAL occurrence, so surface type only.
        print(json.dumps({
            'status': 'ERROR',
            'error_type': type(exc).__name__,
            'apply': apply_send,
            'note': 'Retry the identical native occurrence; never allocate a new event ID.',
        }, indent=2))
        return 1

    result = dict(result)
    result['apply'] = apply_send
    # A send/ACK is never proof the human saw it; only a live client check is.
    result['desktop_visibility_verified'] = False
    print(json.dumps(result, indent=2, sort_keys=True))
    return EXIT_CODES.get(result.get('status'), 1)


if __name__ == '__main__':
    raise SystemExit(main())
