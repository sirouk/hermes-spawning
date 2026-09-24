"""Post into a Hermes Desktop Group Chat room and drive the member round.

VERIFIED WORKING against Hermes 0.21.4 (desktop source v2026.9.14). A post
written by this module plus a member reply it drove both rendered in Desktop.

Reverse-engineered from apps/desktop/src/plugins/hermes-bots:
  group-rounds.ts  sendToGroupChat()  -> appends a user entry, then drives
  group-turns.ts   ensureGroupChatSession()/prompt.submit -> per-member turns
  group-chat.ts    scheduleGroupChatServerSync() -> profiles.configure ui_meta
                   with ui_meta_expected_revisions (CAS)

Desktop's group chat is CLIENT-SIDE: it does not use groups.send / the hosted
room engine at all. The room the human watches is the ui_meta projection under
the DEFAULT profile's `hermes-bots-groups` key. Member replies are produced by
submitting a prompt into each member profile's own hidden "Group: ..." session
and reading the reply back off session.resume.

`rpc` is any callable `rpc(method: str, params: dict) -> dict` bound to an
authenticated gateway JSON-RPC session (see the skill's transport rules; reuse
the existing credential-safe adapter, never re-implement auth).

Usage sketch:

    snap, rev = read_projection(rpc)
    room = snap["rooms"]["name:My Room"]
    thread = mint_thread_id()
    append_entry(room, {"kind": "user", "name": "You"}, "hello", thread)
    room["revision"] = int(room.get("revision") or 0) + 1
    snap["updatedAt"] = now_ms()
    write_projection(rpc, snap, rev)          # CAS on the revision just read
"""

from __future__ import annotations

import math
import re
import time
import uuid
from typing import Any

META_KEY = "hermes-bots-groups"
TURN_POLL_SECONDS = 5
TURN_HARD_CAP_S = 20 * 60
class RoundIncomplete(RuntimeError):
    """Some member replies remain unresolved; already-posted entries stay posted.

    `unfinished` names members whose turns timed out or were never submitted;
    `appended` contains verified room entries from this attempt. Inspect the
    hidden sessions by title before manually harvesting, not resubmitting.
    """

    def __init__(self, unfinished: list[str], appended: list[dict]):
        self.unfinished = tuple(unfinished)
        self.appended = list(appended)
        super().__init__(
            f"room round incomplete; unfinished members: {', '.join(unfinished)}; "
            "inspect member sessions before any retry"
        )


# group-round-prompt.ts MEMBER_CONTROL_FRAME_RE. A member reply is republished
# to peers inside a role=user prompt, so a reply reproducing a control frame
# would read as harness input. Relabel it.
_CONTROL_FRAME_RE = re.compile(
    r"\[(?=/?OUT-OF-BAND USER MESSAGE|CONTEXT COMPACTION|CONTEXT SUMMARY\]|PRIOR CONTEXT"
    r"|Runtime note:|System note:|System:|SYSTEM\]|IMPORTANT:|Planning state preserved"
    r"|ASYNC DELEGATION)",
    re.I,
)


def _relabel(text: str) -> str:
    return _CONTROL_FRAME_RE.sub("[member-quoted ", text or "")


def mint_thread_id() -> str:
    """group-chat.ts mintGroupThreadId(): 'tm' + base36 time + '-' + rand."""
    return f"tm{int(time.time() * 1000):x}-{uuid.uuid4().hex[:5]}"


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------
# ui_meta projection (what Desktop renders)
# --------------------------------------------------------------------------

def read_projection(rpc) -> tuple[dict[str, Any], int]:
    """groupChatRemoteSnapshot(): profiles.list -> default profile ui_meta.

    Returns (snapshot, cas_revision). Always pass that exact revision back to
    write_projection: it is the compare-and-set fence.
    """
    res = rpc("profiles.list", {"include_sessions": False}) or {}
    profiles = res.get("profiles") or []
    default = next((p for p in profiles if p.get("name") == "default"), None)
    if not default:
        raise RuntimeError("no default profile in profiles.list")
    snapshot = (default.get("ui_meta") or {}).get(META_KEY) or {}
    revision = int((default.get("ui_meta_revisions") or {}).get(META_KEY) or 0)
    return snapshot, revision


def write_projection(rpc, snapshot: dict, expected_revision: int) -> dict:
    """flushGroupChatServerSync(): profiles.configure with CAS.

    Rejects any `applied.ui_meta` other than true without echoing the response.
    The failure shape is ambiguous (stale CAS, oversized incoming ui_meta, or
    schema validation). The observed ~64KiB limit measures the character count
    of json.dumps(incoming), not bytes of persisted ui_meta. Do not blind-retry;
    re-read live state for diagnosis and reconcile uncertain outcomes by entry id.
    """
    params = {
        "name": "default",
        "ui_meta": {META_KEY: snapshot},
        "ui_meta_expected_revisions": {META_KEY: expected_revision},
    }
    res = rpc("profiles.configure", params) or {}
    applied = res.get("applied") or {}
    if applied.get("ui_meta") is not True:
        raise RuntimeError(
            "ui_meta write not applied (cause unknown); inspect fresh projection "
            "and incoming payload size; do not retry or assume a CAS conflict"
        )
    return res


def assert_room_live(snapshot: dict, room_key: str) -> dict:
    """Refuse to post into a room the user closed.

    A key present in `deleted` carries the revision at which it died; re-adding
    it is a resurrection the merge is designed to drop. Check before writing.
    """
    rooms = snapshot.get("rooms") or {}
    deleted = snapshot.get("deleted") or {}
    if room_key in deleted:
        raise RuntimeError(f"room {room_key!r} is tombstoned at revision {deleted[room_key]}")
    if room_key not in rooms:
        raise RuntimeError(f"room {room_key!r} not in projection; have {sorted(rooms)}")
    return rooms[room_key]


def append_entry(room: dict, frm: dict, text: str, thread: str) -> dict:
    """group-chat.ts appendGroupChatEntry() -- id/from/text/at/thread.

    `frm` is {"kind":"user","name":"You"} or
    {"kind":"member","name":"<profile>","source":"<label>"}.
    `at` is epoch MILLISECONDS. Entry identity for merge is `id:<uuid>`.
    """
    entry = {
        "id": str(uuid.uuid4()),
        "from": frm,
        "text": text,
        "at": now_ms(),
        "thread": thread,
    }
    room.setdefault("log", []).append(entry)
    return entry


def post(rpc, room_key: str, text: str, frm: dict | None = None,
         thread: str | None = None) -> tuple[dict, str]:
    """One complete read-append-bump-write-verify cycle. Returns (entry, thread)."""
    snapshot, revision = read_projection(rpc)
    room = assert_room_live(snapshot, room_key)
    target = thread or mint_thread_id()
    entry = append_entry(room, frm or {"kind": "user", "name": "You"}, text, target)
    room["revision"] = int(room.get("revision") or 0) + 1
    snapshot["updatedAt"] = now_ms()
    write_projection(rpc, snapshot, revision)

    # An ACK is not a write. Read back and prove the entry landed.
    after, new_revision = read_projection(rpc)
    log = (after.get("rooms") or {}).get(room_key, {}).get("log") or []
    if not any(e.get("id") == entry["id"] for e in log):
        raise RuntimeError("entry absent from projection after write")
    if new_revision <= revision:
        raise RuntimeError(f"CAS revision did not advance ({revision} -> {new_revision})")
    return entry, target


# --------------------------------------------------------------------------
# member turns (what makes bots actually reply)
# --------------------------------------------------------------------------

def format_line(entry: dict, viewer_name: str) -> str:
    """group-round-prompt.ts formatGroupChatLine()."""
    frm = entry.get("from") or {}
    text = entry.get("text") or ""
    if frm.get("kind") == "user":
        return f"{frm.get('name') or 'User'} (user): {text}"
    suffix = " (you)" if frm.get("name") == viewer_name else ""
    source = f" [{frm['source']}]" if frm.get("source") else ""
    return f"{frm.get('name')}{suffix}{source}: {_relabel(text)}"


def build_turn_prompt(group_name: str, members: list[dict], viewer: dict,
                      delta_lines: list[str]) -> str:
    """group-round-prompt.ts buildGroupChatTurnPrompt() -- rules verbatim.

    The participation rules travel in the turn payload, NOT in SOUL.md, so any
    existing bot can join a room with no profile migration.
    """
    viewer_h = viewer.get("handle") or viewer.get("name")
    peers = [m for m in members if (m.get("handle") or m.get("name")) != viewer_h]
    peer_names = ", ".join(f"@{m.get('handle') or m.get('name')}" for m in peers)
    return "\n".join([
        f'[Group chat: "{group_name}"] You are @{viewer_h}, one participant in a '
        f'group chat with {peer_names or "no one else yet"} and the user.',
        "",
        "New messages in the room since your last turn (oldest first):",
        *[f"  {line}" for line in delta_lines],
        "",
        "Rules for this room:",
        "- Reply with ONE conversational message ONLY if you have something new worth "
        "adding: build on what was just said, claim or hand off work, answer a question "
        "aimed at you, or report a real result. Keep chatter short (1-3 sentences) - but "
        "when you are delivering a result, an answer the user asked for, or substantive "
        "work, give it at full quality and length; never thin out real content to fit the room.",
        '- If you have nothing new to add, reply with exactly "(pass)". Passing is good - '
        "it lets the conversation settle.",
        "- Mention a teammate as @name to pull them in; mention @user only for a judgment "
        "call or a result the user needs. Do not repeat points already made.",
        "- Never reveal content from your private 1:1 chats. Your reply text goes to the "
        "room verbatim - no preamble, no meta-commentary.",
    ])


def ensure_member_session(rpc, member: dict, room_id: str,
                          thread: str) -> tuple[str, str | None]:
    """group-turns.ts ensureGroupChatSession(): resume by title, else create.

    Returns (runtime_id, stored_id). Session title contract:
    `Group: <roomId or displayName> - <thread>` (the separator is U+00B7).
    Room plumbing sessions are ALWAYS hidden=1 -- never 'fix' that.
    """
    profile = member.get("name")
    title = f"Group: {room_id} \u00b7 {thread}"
    try:
        res = rpc("session.resume", {
            "session_id": title, "profile": profile, "omit_messages": True,
        }) or {}
        # The DURABLE id is stored_session_id (what session.resume takes);
        # session_id here is the runtime handle. Returning session_id as the
        # runtime handle breaks the drive_round mapping (resume id went to
        # prompt.submit, runtime-recovery id went to the poll). Same trap as
        # the create path below.
        stored = res.get("stored_session_id")
        if stored:
            return res.get("session_id"), stored
    except Exception:
        pass  # 4007 = genuinely absent -> create

    created = rpc("session.create", {
        "profile": profile,
        "title": title,
        "hidden": True,
        "room_plumbing": True,
        "follow_profile_config": True,
    }) or {}
    # session.create returns BOTH ids: `session_id` is the ephemeral RUNTIME
    # handle (what prompt.submit takes) and `stored_session_id` is the durable
    # id (what session.resume takes). Resuming by the runtime id raises 4007.
    runtime = created.get("session_id")
    stored = created.get("stored_session_id")
    if not runtime:
        raise RuntimeError(f"session.create returned no session_id for {profile}")
    return runtime, stored


def _is_busy(state: dict) -> bool:
    """group-turns.ts groupSessionBusy()."""
    if not state:
        return False
    if state.get("busy") or state.get("running") or state.get("in_flight"):
        return True
    inflight = state.get("inflight")
    if isinstance(inflight, dict) and inflight:
        return True
    if isinstance(inflight, list) and len(inflight):
        return True
    return False


def _assistant_text(message: dict) -> str:
    """session.resume returns messages as {role, text}, NOT {role, content}.

    Reading `content` yields empty strings and looks exactly like "the bot
    never replied" when it did. Read `text` first.
    """
    content = message.get("text")
    if content is None:
        content = message.get("content")
    if isinstance(content, list):
        content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return (content or "").strip()


def run_member_turn(rpc, member: dict, submit_id: str, resume_id: str,
                    prompt: str, deadline: float) -> str | None:
    """prompt.submit on the id that accepts submits, poll session.resume on the
    id that resumes. drive_round names the two stored/runtime handles for these
    exact roles; do NOT re-derive them here.

    Recovery inverts a stale runtime handle, so submit_id may be a plain
    session id, a `runtime:...` handle, or a `stored:<id>` handle. submit only
    pushes; the poll on resume_id is the source of truth for the reply.

    `deadline` is an absolute time.monotonic() value. Returns newly observed
    assistant text, or None on timeout / empty. A literal "(pass)" is an
    observed reply meaning 'nothing to add' -- do not append it to the room.
    """
    return _poll_member_reply(rpc, member, submit_id, resume_id, prompt, deadline)


def drive_round(rpc, room_key: str, members: list[dict], thread: str,
                delta_lines: list[str], per_member_budget_s: int = 420,
                *, round_budget_s: float | None = None,
                min_member_budget_s: float | None = None) -> list[dict]:
    """Run a serial round under a shared MONOTONIC wall-clock deadline.

    Preserve the old default TOTAL budget: 420 seconds per member times the
    roster size (420 * N seconds by default, not 420 seconds total). A caller
    with a tighter scheduler deadline should pass the round's total available
    seconds via `round_budget_s`. Before EACH submit, recalculate
    min(remaining, max(floor, remaining / remaining_members)); saved time from
    quick speakers may be given to a slower tail. The default floor is one
    poll interval (or the legacy per-member budget, if shorter); it is
    capped by the initial fair slice so a short round reserves tail time. A
    floor never grants time past the round deadline. Synchronous RPCs must themselves
    have transport timeouts: this cooperative deadline cannot interrupt a hung
    RPC. Do not interpret an unfinished turn as `(pass)`; inspect the member's
    title-keyed session before deciding whether a late reply needs harvesting.

    Successful non-pass replies are appended and read back independently. If
    any turn times out, raise RoundIncomplete with the unfinished roster and
    already-appended entries; never silently skip the tail decision maker.
    """
    if not members:
        return []
    if (not isinstance(per_member_budget_s, (int, float))
            or not math.isfinite(per_member_budget_s)
            or per_member_budget_s <= 0):
        raise ValueError("per-member baseline budget must be finite positive seconds")
    total_budget = (per_member_budget_s * len(members)
                    if round_budget_s is None else round_budget_s)
    requested_floor = (min(per_member_budget_s, TURN_POLL_SECONDS)
                       if min_member_budget_s is None else min_member_budget_s)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0
               for v in (total_budget, requested_floor)):
        raise ValueError("round and floor budgets must be finite positive seconds")
    # Leave one fair slice for each remaining member; a large configured floor
    # cannot consume the whole short round on its first iteration.
    floor = min(requested_floor, total_budget / len(members))

    round_deadline = time.monotonic() + total_budget
    appended: list[dict] = []
    unfinished: list[str] = []
    for index, member in enumerate(members):
        name = member.get("name") or f"member #{index + 1}"
        if time.monotonic() >= round_deadline:
            unfinished.extend(m.get("name") or f"member #{i + 1}"
                              for i, m in enumerate(members[index:], index))
            break
        snapshot, _ = read_projection(rpc)
        room = assert_room_live(snapshot, room_key)
        room_id = room.get("roomId") or room.get("name") or room_key
        group_name = room.get("name") or room_key

        # runtime is what prompt.submit accepts; title is the stable baseline
        # for resume/poll, even if a stored id is moved or runtime-recovered.
        runtime, _stored = ensure_member_session(rpc, member, room_id, thread)
        title = f"Group: {room_id} \u00b7 {thread}"
        prompt = build_turn_prompt(group_name, members, member, delta_lines)

        remaining_time = round_deadline - time.monotonic()
        if remaining_time <= 0:
            unfinished.extend(m.get("name") or f"member #{i + 1}"
                              for i, m in enumerate(members[index:], index))
            break
        remaining_members = len(members) - index
        slice_s = min(remaining_time,
                      max(floor, remaining_time / remaining_members))
        reply = run_member_turn_by_title(rpc, member, title, runtime, prompt,
                                         min(round_deadline, time.monotonic() + slice_s))
        if reply is None:
            unfinished.append(name)
            continue
        if reply.strip() == "(pass)":
            continue  # only an actual completed assistant reply counts as a pass

        entry, _ = post(rpc, room_key, reply, frm={
            "kind": "member", "name": name,
            **({"source": member["source"]} if member.get("source") else {}),
        }, thread=thread)
        appended.append(entry)
        delta_lines = delta_lines + [format_line(entry, name)]

    if unfinished:
        raise RoundIncomplete(unfinished, appended)
    return appended


def run_member_turn_by_title(rpc, member: dict, title: str, submit_id: str,
                             prompt: str, deadline: float) -> str | None:
    """Poll by stable session TITLE, not a possibly-moved stored session id.

    `deadline` is an absolute time.monotonic() deadline. None means no newly
    observed assistant text by the deadline (NOT a literal `(pass)`).
    """
    return _poll_member_reply(rpc, member, submit_id, title, prompt, deadline)


def _poll_member_reply(rpc, member: dict, submit_id: str, resume_key: str,
                       prompt: str, deadline: float) -> str | None:
    """Poll a submitted turn; non-busy alone is never proof of completion.

    A fresh nonempty assistant message observed after our baseline is the
    available completion proxy; Hermes lacks an explicit turn-complete edge.
    Bound each sleep by the remaining slice and read once at its boundary.
    RPC calls must have their own transport timeout; a synchronous call cannot
    be interrupted by this cooperative wall-clock deadline.
    """
    profile = member.get("name")
    if time.monotonic() >= deadline:
        return None  # do not submit a new prompt past the shared deadline
    params = {"session_id": resume_key, "profile": profile}
    pre = rpc("session.resume", params) or {}
    before = len(pre.get("messages") or [])
    if time.monotonic() >= deadline:
        return None
    rpc("prompt.submit", {"session_id": submit_id, "text": prompt})

    while True:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(TURN_POLL_SECONDS, remaining))
        # A final read at the deadline catches answers finished in a short
        # slice (< one normal poll tick). It cannot extend the sleep budget.
        state = rpc("session.resume", params) or {}
        msgs = state.get("messages") or []
        if not _is_busy(state):
            for m in reversed(msgs[before:]):
                if m.get("role") == "assistant":
                    text = _assistant_text(m)
                    if text:
                        return text
        # Growth without assistant text (often a tool call) and busy:false
        # without growth BOTH mean "unresolved", not completed or `(pass)`.
        if time.monotonic() >= deadline:
            return None
