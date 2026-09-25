#!/usr/bin/env python3
"""Sparse, append-only convergence memory for Hermes fleets.

Exit codes: 0 success/guard allowed; 2 invalid input or storage error;
3 guard denied an unchanged known failure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Iterable
import uuid


SCHEMA_VERSION = 1
DEFAULT_DB = Path("/opt/data/fleet-state/convergence.db")
OUTCOMES = frozenset({"success", "failure", "mixed", "unknown"})
PATTERN_KINDS = frozenset({"prefer", "avoid", "invariant"})
LOCATOR_RE = re.compile(r"^[a-z][a-z0-9+.-]{1,31}:[^\s].*$", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/+-]{1,63}", re.IGNORECASE)
LIMITS = {
    "goal": 160,
    "scope": 160,
    "actor": 120,
    "approach": 360,
    "summary": 800,
    "failure_signature": 240,
    "guidance": 600,
    "curator": 120,
    "json": 4096,
    "source": 512,
}


class InputError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def bounded(label: str, value: str, *, allow_empty: bool = False) -> str:
    value = value.strip()
    if (not value and not allow_empty) or len(value) > LIMITS[label]:
        qualifier = "nonempty and " if not allow_empty else ""
        raise InputError(f"{label} must be {qualifier}at most {LIMITS[label]} characters")
    if "\n" in value or "\r" in value or "\x00" in value:
        raise InputError(f"{label} must be one line without NUL")
    return value


def json_object(label: str, raw: str | None) -> dict[str, Any]:
    raw = raw or "{}"
    if len(raw) > LIMITS["json"]:
        raise InputError(f"{label} JSON exceeds {LIMITS['json']} characters")
    try:
        value = json.loads(raw)
        canonical(value)
    except (ValueError, TypeError) as exc:
        raise InputError(f"{label} must be finite valid JSON") from exc
    if not isinstance(value, dict):
        raise InputError(f"{label} must be a JSON object")
    return value


def numeric_rewards(raw: str | None) -> dict[str, float]:
    value = json_object("rewards", raw)
    if len(value) > 24 or any(not isinstance(k, str) or not k or len(k) > 80
                              or isinstance(v, bool) or not isinstance(v, (int, float))
                              for k, v in value.items()):
        raise InputError("rewards must contain at most 24 named numeric dimensions")
    return {key: float(number) for key, number in value.items()}


def sources(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = bounded("source", raw)
        if not LOCATOR_RE.fullmatch(value):
            raise InputError(f"source is not a stable scheme:locator reference: {value!r}")
        if value not in result:
            result.append(value)
    if not result or len(result) > 12:
        raise InputError("provide 1-12 distinct source locators")
    return result


def digest(*values: str) -> str:
    framed = "".join(f"{len(value)}:{value}" for value in values)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def tokens(value: Any) -> set[str]:
    text = value if isinstance(value, str) else canonical(value)
    return {token.lower() for token in TOKEN_RE.findall(text)}


def connect(path: Path) -> sqlite3.Connection:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS ledger_meta(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS episodes(
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        goal TEXT NOT NULL,
        scope TEXT NOT NULL,
        actor TEXT NOT NULL,
        approach TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK(outcome IN ('success','failure','mixed','unknown')),
        summary TEXT NOT NULL,
        conditions_json TEXT NOT NULL,
        conditions_hash TEXT NOT NULL,
        rewards_json TEXT NOT NULL,
        sources_json TEXT NOT NULL,
        failure_signature TEXT,
        versions_json TEXT NOT NULL,
        novelty_key TEXT NOT NULL UNIQUE
      );
      CREATE INDEX IF NOT EXISTS episodes_comparable
        ON episodes(goal, scope, conditions_hash, created_at);
      CREATE INDEX IF NOT EXISTS episodes_failure
        ON episodes(goal, scope, failure_signature, conditions_hash)
        WHERE outcome='failure';
      CREATE TABLE IF NOT EXISTS patterns(
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        goal TEXT NOT NULL,
        scope TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('prefer','avoid','invariant')),
        conditions_json TEXT NOT NULL,
        guidance TEXT NOT NULL,
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        fresh_until TEXT,
        versions_json TEXT NOT NULL,
        curator TEXT NOT NULL,
        supersedes TEXT REFERENCES patterns(id),
        FOREIGN KEY(supersedes) REFERENCES patterns(id)
      );
      CREATE INDEX IF NOT EXISTS patterns_lookup ON patterns(goal, scope, created_at);
      CREATE TABLE IF NOT EXISTS pattern_evidence(
        pattern_id TEXT NOT NULL REFERENCES patterns(id),
        episode_id TEXT NOT NULL REFERENCES episodes(id),
        stance TEXT NOT NULL CHECK(stance IN ('supports','contradicts')),
        PRIMARY KEY(pattern_id, episode_id)
      );
      CREATE TRIGGER IF NOT EXISTS episodes_no_update
        BEFORE UPDATE ON episodes BEGIN SELECT RAISE(ABORT, 'episodes are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS episodes_no_delete
        BEFORE DELETE ON episodes BEGIN SELECT RAISE(ABORT, 'episodes are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS patterns_no_update
        BEFORE UPDATE ON patterns BEGIN SELECT RAISE(ABORT, 'patterns are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS patterns_no_delete
        BEFORE DELETE ON patterns BEGIN SELECT RAISE(ABORT, 'patterns are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS evidence_no_update
        BEFORE UPDATE ON pattern_evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
      CREATE TRIGGER IF NOT EXISTS evidence_no_delete
        BEFORE DELETE ON pattern_evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
    """)
    row = db.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()
    if row is None:
        db.execute("INSERT INTO ledger_meta(key,value) VALUES('schema_version',?)",
                   (str(SCHEMA_VERSION),))
        db.commit()
    elif row[0] != str(SCHEMA_VERSION):
        raise InputError(f"unsupported ledger schema {row[0]}")
    return db


def emit(value: Any) -> None:
    print(canonical(value))


def record(db: sqlite3.Connection, args: argparse.Namespace) -> int:
    goal = bounded("goal", args.goal)
    scope = bounded("scope", args.scope)
    actor = bounded("actor", args.actor)
    approach = bounded("approach", args.approach)
    summary = bounded("summary", args.summary)
    if args.outcome not in OUTCOMES:
        raise InputError("invalid outcome")
    conditions = json_object("conditions", args.conditions)
    rewards = numeric_rewards(args.rewards)
    versions = json_object("versions", args.versions)
    refs = sources(args.source)
    signature = None
    if args.failure_signature:
        signature = bounded("failure_signature", args.failure_signature).lower()
    if args.outcome == "failure" and not signature:
        raise InputError("failure outcomes require --failure-signature")
    condition_text = canonical(conditions)
    key = digest(goal, scope, actor, approach, args.outcome, summary, condition_text,
                 canonical(rewards), canonical(refs), signature or "", canonical(versions))
    prior = db.execute("SELECT id,created_at FROM episodes WHERE novelty_key=?", (key,)).fetchone()
    if prior:
        emit({"ok": True, "deduplicated": True, "episode_id": prior["id"],
              "created_at": prior["created_at"]})
        return 0
    episode_id = args.episode_id or f"ep_{uuid.uuid4().hex}"
    created = now_iso()
    with db:
        db.execute("""INSERT INTO episodes
          (id,created_at,goal,scope,actor,approach,outcome,summary,conditions_json,
           conditions_hash,rewards_json,sources_json,failure_signature,versions_json,novelty_key)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (episode_id, created, goal, scope, actor, approach, args.outcome, summary,
           condition_text, digest(condition_text), canonical(rewards), canonical(refs),
           signature, canonical(versions), key))
    emit({"ok": True, "deduplicated": False, "episode_id": episode_id,
          "created_at": created})
    return 0


def guard(db: sqlite3.Connection, args: argparse.Namespace) -> int:
    goal = bounded("goal", args.goal)
    scope = bounded("scope", args.scope)
    signature = bounded("failure_signature", args.failure_signature).lower()
    conditions = json_object("conditions", args.conditions)
    condition_hash = digest(canonical(conditions))
    rows = db.execute("""SELECT id,created_at,sources_json FROM episodes
        WHERE goal=? AND scope=? AND outcome='failure' AND failure_signature=?
          AND conditions_hash=? ORDER BY created_at DESC,id DESC LIMIT 8""",
        (goal, scope, signature, condition_hash)).fetchall()
    if rows:
        emit({"allowed": False, "reason": "known_failure_unchanged_conditions",
              "failure_signature": signature,
              "matching_episode_ids": [row["id"] for row in rows],
              "sources": sorted({source for row in rows
                                   for source in json.loads(row["sources_json"])})[:12],
              "required_next_move": "change a relevant condition, choose another approach, or escalate"})
        return 3
    changed = db.execute("""SELECT id,conditions_hash,created_at FROM episodes
        WHERE goal=? AND scope=? AND outcome='failure' AND failure_signature=?
        ORDER BY created_at DESC,id DESC LIMIT 8""", (goal, scope, signature)).fetchall()
    emit({"allowed": True,
          "reason": "no_matching_failure" if not changed else "conditions_changed",
          "prior_failure_episode_ids": [row["id"] for row in changed]})
    return 0


def pattern_rows(db: sqlite3.Connection, goal: str, scope: str) -> list[sqlite3.Row]:
    return db.execute("""SELECT p.*,
          SUM(CASE WHEN e.stance='supports' THEN 1 ELSE 0 END) AS supports,
          SUM(CASE WHEN e.stance='contradicts' THEN 1 ELSE 0 END) AS contradicts
        FROM patterns p LEFT JOIN pattern_evidence e ON e.pattern_id=p.id
        WHERE p.goal=? AND p.scope IN (?, '*')
          AND NOT EXISTS (SELECT 1 FROM patterns newer WHERE newer.supersedes=p.id)
        GROUP BY p.id ORDER BY p.created_at DESC,p.id DESC LIMIT 256""",
        (goal, scope)).fetchall()


def recall(db: sqlite3.Connection, args: argparse.Namespace) -> int:
    goal = bounded("goal", args.goal)
    scope = bounded("scope", args.scope)
    current = json_object("conditions", args.conditions)
    query = bounded("summary", args.query or "", allow_empty=True)
    now = datetime.now(timezone.utc)
    current_tokens = tokens(current)
    query_tokens = tokens(query)
    candidates = []
    stale = 0
    for row in pattern_rows(db, goal, scope):
        if row["fresh_until"]:
            expiry = datetime.fromisoformat(row["fresh_until"].replace("Z", "+00:00"))
            if expiry <= now:
                stale += 1
                continue
        pattern_conditions = json.loads(row["conditions_json"])
        p_tokens = tokens(pattern_conditions)
        applicability = len(current_tokens & p_tokens) / max(1, len(p_tokens))
        text_tokens = tokens(row["guidance"]) | p_tokens
        text_fit = len(query_tokens & text_tokens) / max(1, len(query_tokens)) if query_tokens else 0.0
        supports = int(row["supports"] or 0)
        contradicts = int(row["contradicts"] or 0)
        evidence_fit = supports / max(1, supports + contradicts)
        exact_scope = 1.0 if row["scope"] == scope else 0.0
        score = (4 * applicability + 2 * text_fit + 1.5 * float(row["confidence"])
                 + evidence_fit + .25 * exact_scope)
        candidates.append((score, row, pattern_conditions, supports, contradicts))
    candidates.sort(key=lambda item: (-item[0], item[1]["id"]))
    selected: list[dict[str, Any]] = []
    used = 0
    for score, row, conditions, supports, contradicts in candidates:
        evidence = db.execute("""SELECT pe.episode_id,pe.stance,ep.sources_json
            FROM pattern_evidence pe JOIN episodes ep ON ep.id=pe.episode_id
            WHERE pe.pattern_id=? ORDER BY pe.stance DESC,pe.episode_id""",
            (row["id"],)).fetchall()
        item = {"id": row["id"], "kind": row["kind"], "conditions": conditions,
                "guidance": row["guidance"], "confidence": row["confidence"],
                "fresh_until": row["fresh_until"], "versions": json.loads(row["versions_json"]),
                "supporting_episode_ids": [e["episode_id"] for e in evidence if e["stance"] == "supports"],
                "contradicting_episode_ids": [e["episode_id"] for e in evidence if e["stance"] == "contradicts"],
                "source_locators": sorted({source for e in evidence
                                            for source in json.loads(e["sources_json"])})[:12],
                "rank_score": round(score, 6), "authority": "advisory"}
        size = len(canonical(item))
        if size > args.char_budget:
            continue
        if used + size > args.char_budget:
            break
        selected.append(item)
        used += size
        if len(selected) >= args.limit:
            break
    emit({"goal": goal, "scope": scope, "patterns": selected,
          "returned": len(selected), "characters": used,
          "stale_patterns_omitted": stale,
          "notice": "Advisory experience only; current authoritative observation wins."})
    return 0


def compare(db: sqlite3.Connection, args: argparse.Namespace) -> int:
    goal = bounded("goal", args.goal)
    scope = bounded("scope", args.scope)
    conditions = json_object("conditions", args.conditions) if args.conditions else None
    params: list[Any] = [goal, scope]
    predicate = "goal=? AND scope=?"
    if conditions is not None:
        predicate += " AND conditions_hash=?"
        params.append(digest(canonical(conditions)))
    rows = db.execute(f"""SELECT id,created_at,actor,approach,outcome,summary,
                                  conditions_hash,rewards_json,sources_json
                           FROM episodes WHERE {predicate}
                           ORDER BY conditions_hash,created_at,id LIMIT ?""",
                      (*params, args.limit)).fetchall()
    dimensions = sorted({key for row in rows for key in json.loads(row["rewards_json"])})
    episodes = [{"id": row["id"], "created_at": row["created_at"], "actor": row["actor"],
                 "approach": row["approach"], "outcome": row["outcome"],
                 "observation": row["summary"], "conditions_hash": row["conditions_hash"],
                 "rewards": json.loads(row["rewards_json"]),
                 "source_locators": json.loads(row["sources_json"])} for row in rows]
    emit({"goal": goal, "scope": scope, "episodes": episodes,
          "reward_dimensions": dimensions,
          "notice": "GRPO-inspired operational comparison; no model weights are trained and no winner is inferred."})
    return 0


def propose_pattern(db: sqlite3.Connection, args: argparse.Namespace) -> int:
    goal = bounded("goal", args.goal)
    scope = bounded("scope", args.scope)
    curator = bounded("curator", args.curator)
    guidance = bounded("guidance", args.guidance)
    if args.kind not in PATTERN_KINDS:
        raise InputError("invalid pattern kind")
    if not 0 <= args.confidence <= 1:
        raise InputError("confidence must be between 0 and 1")
    conditions = json_object("conditions", args.conditions)
    versions = json_object("versions", args.versions)
    if not args.support:
        raise InputError("a pattern needs at least one --support episode")
    if set(args.support) & set(args.contradict):
        raise InputError("one episode cannot both support and contradict a pattern")
    episode_ids = list(dict.fromkeys(args.support + args.contradict))
    marks = ",".join("?" for _ in episode_ids)
    episodes = db.execute(f"SELECT id,goal,scope,actor FROM episodes WHERE id IN ({marks})",
                          episode_ids).fetchall()
    if len(episodes) != len(episode_ids):
        raise InputError("all evidence episode IDs must exist")
    if any(row["goal"] != goal or row["scope"] != scope for row in episodes):
        raise InputError("pattern evidence must share the pattern goal and scope")
    supporters = [row for row in episodes if row["id"] in set(args.support)]
    if any(row["actor"] == curator for row in supporters):
        raise InputError("the curator must be independent of every supporting episode actor")
    if args.supersedes:
        prior = db.execute("SELECT goal,scope FROM patterns WHERE id=?", (args.supersedes,)).fetchone()
        if not prior or prior["goal"] != goal or prior["scope"] != scope:
            raise InputError("superseded pattern must exist with the same goal and scope")
    fresh_until = None
    if args.fresh_until:
        try:
            dt = datetime.fromisoformat(args.fresh_until.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InputError("fresh-until must be an ISO-8601 timestamp") from exc
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise InputError("fresh-until must include a timezone")
        fresh_until = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    pattern_id = args.pattern_id or f"pat_{uuid.uuid4().hex}"
    created = now_iso()
    with db:
        db.execute("""INSERT INTO patterns
          (id,created_at,goal,scope,kind,conditions_json,guidance,confidence,
           fresh_until,versions_json,curator,supersedes)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          (pattern_id, created, goal, scope, args.kind, canonical(conditions), guidance,
           args.confidence, fresh_until, canonical(versions), curator, args.supersedes))
        db.executemany("INSERT INTO pattern_evidence(pattern_id,episode_id,stance) VALUES(?,?,?)",
                       [(pattern_id, episode, "supports") for episode in args.support]
                       + [(pattern_id, episode, "contradicts") for episode in args.contradict])
    emit({"ok": True, "pattern_id": pattern_id, "created_at": created,
          "authority": "advisory"})
    return 0


def stats(db: sqlite3.Connection, _args: argparse.Namespace) -> int:
    episode_counts = {row["outcome"]: row["count"] for row in
                      db.execute("SELECT outcome,count(*) AS count FROM episodes GROUP BY outcome")}
    emit({"schema": SCHEMA_VERSION,
          "episodes": sum(episode_counts.values()), "outcomes": episode_counts,
          "patterns": db.execute("SELECT count(*) FROM patterns").fetchone()[0],
          "active_patterns": db.execute("""SELECT count(*) FROM patterns p WHERE NOT EXISTS
              (SELECT 1 FROM patterns newer WHERE newer.supersedes=p.id)""").fetchone()[0]})
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--db", type=Path,
                      default=Path(os.environ.get("HERMES_CONVERGENCE_DB", DEFAULT_DB)))
    commands = root.add_subparsers(dest="command", required=True)

    record_p = commands.add_parser("record", help="append one material outcome")
    for flag in ("goal", "scope", "actor", "approach", "summary"):
        record_p.add_argument(f"--{flag}", required=True)
    record_p.add_argument("--outcome", choices=sorted(OUTCOMES), required=True)
    record_p.add_argument("--conditions", default="{}")
    record_p.add_argument("--rewards", default="{}")
    record_p.add_argument("--versions", default="{}")
    record_p.add_argument("--source", action="append", required=True)
    record_p.add_argument("--failure-signature")
    record_p.add_argument("--episode-id")
    record_p.set_defaults(handler=record)

    guard_p = commands.add_parser("guard", help="deny an unchanged known failed retry")
    for flag in ("goal", "scope", "failure_signature"):
        guard_p.add_argument(f"--{flag.replace('_', '-')}", dest=flag, required=True)
    guard_p.add_argument("--conditions", default="{}")
    guard_p.set_defaults(handler=guard)

    recall_p = commands.add_parser("recall", help="retrieve a bounded set of applicable patterns")
    recall_p.add_argument("--goal", required=True)
    recall_p.add_argument("--scope", required=True)
    recall_p.add_argument("--conditions", default="{}")
    recall_p.add_argument("--query", default="")
    recall_p.add_argument("--limit", type=int, default=5, choices=range(1, 21))
    recall_p.add_argument("--char-budget", type=int, default=4000, choices=range(256, 16001))
    recall_p.set_defaults(handler=recall)

    compare_p = commands.add_parser("compare", help="show like-for-like approaches and rewards")
    compare_p.add_argument("--goal", required=True)
    compare_p.add_argument("--scope", required=True)
    compare_p.add_argument("--conditions")
    compare_p.add_argument("--limit", type=int, default=50, choices=range(1, 501))
    compare_p.set_defaults(handler=compare)

    pattern_p = commands.add_parser("propose-pattern", help="append an independently curated pattern")
    for flag in ("goal", "scope", "curator", "guidance"):
        pattern_p.add_argument(f"--{flag}", required=True)
    pattern_p.add_argument("--kind", choices=sorted(PATTERN_KINDS), required=True)
    pattern_p.add_argument("--conditions", default="{}")
    pattern_p.add_argument("--versions", default="{}")
    pattern_p.add_argument("--confidence", required=True, type=float)
    pattern_p.add_argument("--fresh-until")
    pattern_p.add_argument("--support", action="append", default=[])
    pattern_p.add_argument("--contradict", action="append", default=[])
    pattern_p.add_argument("--supersedes")
    pattern_p.add_argument("--pattern-id")
    pattern_p.set_defaults(handler=propose_pattern)

    stats_p = commands.add_parser("stats", help="return counts only")
    stats_p.set_defaults(handler=stats)
    return root


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        with connect(args.db) as db:
            return args.handler(db, args)
    except (InputError, sqlite3.Error, OSError) as exc:
        emit({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
