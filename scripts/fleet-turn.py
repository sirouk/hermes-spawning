#!/usr/bin/env python3
"""Bounded driver for one conversational turn inside a Hermes container.

Encodes the formation-turn doctrine from skills/fleet-organism-design:
one driver per session, fast abort on the busy-session banner, stdin prompts
(--query-file resolves inside the container, so pipe instead), and leftover
process cleanup that survives client disconnect by design.

Exit codes: 0 = turn completed; 1 = turn/driver error; 2 = input error;
3 = session busy (another process holds the session); 4 = leftovers refused.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

BUSY_RE = re.compile(r"Another Hermes process is using this session", re.IGNORECASE)
BUSY_REFUSED_RE = re.compile(r"kept this session busy too long", re.IGNORECASE)
CHAT_PROC_RE = re.compile(r"hermes\b.*\bchat\b")


def busy_banner(text: str) -> bool:
    """True when the CLI reported that the session is held by another process."""
    return bool(BUSY_RE.search(text) or BUSY_REFUSED_RE.search(text))


def parse_chat_pids(ps_output: str) -> list[int]:
    """PIDs of hermes chat processes from `ps -eo pid,args` output."""
    pids: list[int] = []
    for line in ps_output.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        if CHAT_PROC_RE.search(parts[1]):
            pids.append(int(parts[0]))
    return pids


def _run_docker(args: list[str], *, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", *args], input=stdin, capture_output=True, check=False,
    )


def leftover_chat_pids(container: str) -> list[int]:
    res = _run_docker([container, "ps", "-eo", "pid,args"])
    if res.returncode != 0:
        return []
    return parse_chat_pids(res.stdout.decode(errors="replace"))


def kill_pids(container: str, pids: list[int]) -> None:
    if not pids:
        return
    # Root + privileges: a plain docker exec kill is denied by the container
    # user namespace (hermes runs as UID 10000).
    res = _run_docker(["--privileged", "-u", "0", container, "kill", "-9", *[str(p) for p in pids]])
    if res.returncode != 0:
        sys.stderr.write(res.stderr.decode(errors="replace"))
        sys.exit(1)


def drive_turn(container: str, chat_args: list[str], prompt: str) -> int:
    """Run one turn. Prompt goes through stdin; abort fast on busy banner."""
    proc = subprocess.Popen(
        ["docker", "exec", "-i", container, "hermes", "chat", "--query-file", "-", *chat_args],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    out, _ = proc.communicate(input=prompt.encode())
    text = out.decode(errors="replace")
    sys.stdout.write(text)
    if busy_banner(text):
        sys.stderr.write("this session is held by another Hermes process; aborting (rc=3)\n")
        return 3
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("container")
    p.add_argument("--prompt-file", help="host-side path; piped to the container over stdin")
    p.add_argument("--prompt", default=None, help="literal prompt text (use with a pipe-up prompt)")
    p.add_argument("--kill-leftovers", action="store_true",
                   help="kill stale hermes chat processes inside the container before launching")
    p.add_argument("--chat-arg", action="append", default=[],
                   help="extra hermes chat argument (repeatable), e.g. --resume <session>")
    args = p.parse_args(argv)

    pids = leftover_chat_pids(args.container)
    if pids and not args.kill_leftovers:
        sys.stderr.write(
            f"refusing to launch: {len(pids)} leftover hermes chat process(es) in "
            f"{args.container} (pids: {' '.join(map(str, pids))}); rerun with --kill-leftovers\n")
        return 4
    kill_pids(args.container, pids)

    if args.prompt is None and args.prompt_file is None:
        sys.stderr.write("one of --prompt or --prompt-file is required\n")
        return 2
    prompt = args.prompt if args.prompt is not None else open(args.prompt_file, encoding="utf-8").read()
    return drive_turn(args.container, args.chat_arg, prompt)


if __name__ == "__main__":
    sys.exit(main())
