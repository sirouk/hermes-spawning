---
name: fleet-convergence-learning
description: Record sparse fleet outcomes, block unchanged failed retries, compare approaches, and retrieve a few source-linked conditional patterns without loading run history.
version: 1.0.0
author: Chris (sirouk), Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [fleet, memory, convergence, grpo, deduplication, ledger]
    related_skills: [fleet-organism-design]
---

# Fleet Convergence Learning

Use this skill at the decision boundary, not as a journaling ritual. Live,
authoritative observation remains the source of present truth. This ledger
supplies only a few applicable patterns and prevents repetition of a known
failure under unchanged conditions.

The one tool is `scripts/convergence.py`. It uses a SQLite ledger at
`$HERMES_CONVERGENCE_DB` or `/opt/data/fleet-state/convergence.db`; pass `--db`
for another location. It emits bounded JSON. Never paste a transcript, report,
model chain-of-thought, secret, credential, or copied source into it.

## Operating loop

1. **Observe live truth.** Use the mission's authoritative Hermes connector or
   native tool. Retain its stable event/object locator and observation time.
2. **Recall narrowly.** Before a consequential choice, run `recall` with the
   goal, scope, current conditions, and a small `--limit`/`--char-budget`.
   Patterns are advisory and cannot grant authority. Re-check their source and
   applicability against current reality.
3. **Guard retries.** Before repeating a failed action, run `guard` with the
   causal failure signature and canonical current conditions. Exit 3 means the
   same failure is already known and no relevant condition changed. Do not
   retry; change a condition, choose another approach, or escalate.
4. **Act through native tools.** Put ownership and handoff on Kanban; deliberate
   in the room. Do not create an evidence file or cycle packet.
5. **Record sparsely.** Run `record` only for a material outcome useful for
   audit, recovery, deduplication, or learning. Use source locators, a compact
   approach, numeric rewards, and a one-line observation. Exact duplicate
   episodes collapse to the existing ID.
6. **Consolidate independently.** Use `compare` for like-for-like episodes.
   A curator who did not produce the supporting episodes may use
   `propose-pattern`. Preserve supporting and contradicting episode IDs,
   applicability conditions, confidence, freshness, and versions. Do not turn
   every episode into a pattern.

## Commands

Run `python3 scripts/convergence.py COMMAND --help` for exact flags.

- `record`: append one bounded structured episode; outcome is `success`,
  `failure`, `mixed`, or `unknown`.
- `guard`: allow a novel/changed attempt or deny an exact known failed retry.
- `recall`: return at most the requested top patterns within the character
  budget, ranked by condition fit, evidence, confidence, text fit, and
  freshness.
- `compare`: show comparable approaches and numeric reward dimensions without
  inventing a winner or calling operational comparison model training.
- `propose-pattern`: append an independently curated `prefer`, `avoid`, or
  `invariant` pattern; optionally supersede an older pattern.
- `stats`: return counts only.

Conditions must include facts that determine applicability, such as tool/API
version, environment, permission mode, and relevant external state. A changed
label is not a changed condition. A failure signature names the causal shape,
not its prose symptom—for example `desktop-room-seat:unverified-connection-id`,
not an entire error message.

## Write threshold

Write nothing when an ordinary step succeeds and native state already makes
the outcome recoverable. Write one episode when at least one is true:

- the result prevents a costly or unsafe retry;
- alternatives need later comparison;
- an unexpected outcome changes future selection;
- audit or recovery needs a durable source-linked fact; or
- repeated independent evidence can refine a reusable pattern.

The pattern is the memory. The episode ledger is provenance. Neither replaces
live truth, Kanban flow, room deliberation, authorization, or a requested
mission deliverable.
