---
name: fleet-organism-design
description: Design a self-optimizing multi-persona Hermes fleet that learns from its own results.
version: 1.3.0
author: Chris (sirouk), Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [fleet, personas, ooda, grpo, retro, kanban, group-chat, governance, mission-handoff]
    related_skills: [hermes-group-chat-delivery]
---

# Fleet Organism Design

Doctrine for shaping a multi-persona Hermes fleet into one organism: how
responsibility, authority, and learning are arranged so the fleet improves from
its own results.

This is the **design** layer -- what shape the fleet should have. It is not a
repair manual for a job that already exists.

The founding charter is `references/fleet-seed.md`, reproduced verbatim. When
this file and the seed disagree, the seed wins. Read it first; everything below
is how to enact it on Hermes. The doctrine is domain-neutral: the mission
supplied at handoff decides what the fleet works on.

## When to Use

- Founding a new fleet: sending the seed and carrying the mission handoff
  through qualification
- Standing up or restructuring a multi-persona fleet that runs on a schedule
- Deciding what a cycle must produce, and what a retro is allowed to change
- Wiring collective learning: comparing approaches, retaining failures, scoring
- Arranging Group Chat rooms and Kanban boards so coordination has a home
- Auditing a running fleet against its own operating map
- **Don't use for:** getting one post into a room (`hermes-group-chat-delivery`)

## Using the seed

The seed is the founding persona's first message, sent whole. It is
orientation only: the persona holds it in context and replies with exactly
`I'm ready.` Anything else -- tool calls, questions, a summary -- means the
seed was not followed; start a fresh session rather than building on it.

The next message is the mission handoff. The seed then drives four stages,
in order:

1. **Study before asking.** Preserve the seed verbatim with provenance, study
   the mission and the existing environment, and verify installed Hermes
   versions and capabilities. No persona, capability, or schedule changes yet.
2. **Ask only what matters.** One consolidated, numbered set of questions the
   evidence leaves open, each with a recommended option, alternatives, and room
   for a free-form answer -- or a short statement that none remain.
3. **Formation gate.** Draft the mission brief and Fleet Operating Map v0 and
   fix the acceptance contract. Preflight the environment -- native tool calls,
   unattended permissions, and each persona's room path -- with schedules
   paused until it passes. Then form the crew -- personas, SOULs, memory,
   context wiring, boards, rooms, access boundaries, schedules, continuation --
   and pass a bounded coordination cycle in which each persona does its own
   role. A profile or board created is not a qualified persona or fleet. No
   mission-production work before this gate, even when it is read-only.
4. **Mission buildout to qualification.** Build the mission work through the
   formed crew, fault-test, and qualify the whole organism before moving into
   production cadence.

The accepted map records the explicit stage: `DISCOVERY`, `FORMING`,
`FORMATION_PASSED`, mission buildout, then production qualification. Discovery
studies only enough domain detail to design roles, settle essential operator
questions, and identify needed tools. During FORMING, qualify native tools,
roles, identity, memory, connectors, boards, rooms, schedules, stops, and
continuation. Do not dispatch role owners to domain research or build datasets,
scorers, models, tests, scripts, tools, or research publications; labels such as
"read-only", "preparatory", or "unaffected" do not exempt mission work.
Connector authentication for a role is formation; publication through it is
mission work. An explicit operator exception must name its scope and authority.
The steward equips and repairs roles, not their mission deliverables. A blocked
room, worker, or continuation path leaves the crew FORMING, however many
profiles and boards exist. Advance other formation work and escalate only a
precise human-dependent blocker in Holding; never switch tracks to mission work.

Accept `FORMATION_PASSED` only on independent, observed evidence: all six
persistent roles loading their own SOUL and current map, using their required
tools/connectors, retrieving verified memory, and completing a role-owned
formation assignment; both boards and exact-roster rooms read back; actual
bounded native schedule-origin coordination with each persona participating in
its role; failure visibility, a stop, and continuation/recovery. Keep schedules
paused until preflight passes, then run only bounded formation checks. Use the
real room path: `conditional_send` gates hosted `groups.send`, not Desktop
`ui_meta`. Backend readback does not prove Desktop rendering; verify with the
client or mark visibility unknown. Test handoff directories when file-first
handoffs are chosen, not by default. An independent assessor verifies the
evidence; only the operator or the acceptance authority designated in the
operator-approved contract may approve the stage. Passing formation admits
mission buildout, not production release. Where available, enforce the stage at
all mission admission/action paths, including native Kanban, cron, direct
founder and one-shot execution; record uncovered paths instead of claiming
that this prose enforces a security boundary. Material context, roster,
permission, tool, or room-path changes invalidate the relevant evidence.

The seed's `FOR THIS FIRST MESSAGE ONLY` section and the `I'm ready.`
handshake are one-time. Once the map exists, the enduring doctrine lives there
and the orientation stays archived as a source artifact.

## Building the team from the mission

The fleet has six personas, and the founding persona is one of them -- not a
management layer above them. The roster is built backward from the mission
supplied at handoff: its outcomes, circumstances, decisions, work, and the
failures it must prevent. Do not pick a domain or name personas before
studying it, and keep what already works.

Whatever the mission, these **functions** must be owned somewhere. They are a
coverage check, not six job titles; the mission decides how they map onto
personas:

| Function | Owns |
|---|---|
| Sensing | Independent, sourced, timestamped current-state assessment prepared before seeing peers |
| Proposal | The recommended course of action, its shape, and why |
| Challenge | Risks, invalidation conditions, and the strongest case against the proposal |
| Execution | Carrying out the decision and reconciling results against the external record |
| Decision | The decision, the dissent, the invalidation condition, the next action |
| Stewardship | The operating map, the retro cycle, and the health of skills and tools |

One persona can carry more than one function when the mission is narrow, as
long as independent challenge survives. One persona must never own both a
decision and the scoring of that decision -- see the separation rule below.

On Hermes each persona is a profile. A named profile is
`/opt/data/profiles/<id>`; the built-in root profile is `/opt/data` itself, its
id is always `default`, and its handle is always `@hermes`. Give the root a
`display_name` when a single agent runs the box; add named profiles when a real
crew does. Personas address each other by handle through Bot Mode, and that
roster is per-instance by design.

## The fractal loop

**Observe -> Orient -> Decide -> Act -> Observe the result**, at four scales:
the fleet's day, a persona's cycle, one task, and one improvement to the fleet
itself. Same steps, different period and budget. Smaller loops inherit the
purpose, boundaries, constraints, and budget of the loop they serve, and feed
observed results forward.

A loop that observes but never decides is a report. A loop that acts without
observing its own results is a habit. Both are defects, and both are common.

## Anti-theatre, concretely

The standard is: **if a line or an artifact does not change an outcome, it is
cruft.** Applied ruthlessly:

- No ceremonial acknowledgments and no status spam. "Working on it" is noise.
- A send acknowledgment is not delivery. Read back the write on its actual
  surface, distinguish rejected/schema/size writes from stale CAS, and verify
  the message reached the room and settled. Backend readback cannot prove it
  renders in Desktop; observe the client separately or mark visibility unknown.
- Do not document the same instruction in three places. Keep one canonical
  document and point at it.
- A failure becomes a retrievable lesson and a corrective test, not a promise
  to do better.
- Evidence is retained automatically, not performed. If a human must be told
  that work happened, the work did not record itself properly.
- Hash or checksum only what someone will actually verify against. A digest
  nobody checks is decoration.
- One record per piece of work, in its existing card or run -- not a draft, a
  review, a disposition, and a summary of the same small step.
- Say what happened. Do not string "not X, not Y, not Z" caveats onto a
  result; limitations that matter go on the card once.

## Where knowledge lives

Each store has a job. Putting content in the wrong one is a common and
expensive mistake.

- **SOUL.md** -- the persona's inclinations, judgment style, purpose, and
  boundaries. Identity, not procedure. Refine deliberately, on evidence.
- **MEMORY.md** -- compact, verified lessons. It is capped by
  `memory.memory_char_limit` and rides in the prompt on every turn. Over the
  cap, entries still load but every later addition is refused. Keep it short by
  intent: durable knowledge, provenance preserved, conflicts consolidated.
  Orientation belongs here; doctrine does not.
- **Markdown documents** -- structured knowledge that changes as the map
  changes. Canonical documents plus pointers.
- **Skills** -- guided procedures with prerequisites, expected results,
  verification, and failure handling. This file is one.
- **Tools** -- native first. Build a specialty tool for a genuine gap, never a
  parallel orchestration system. Tools need clear inputs and outputs, bounded
  execution, meaningful errors, and a check that detects breakage before
  consequential use.

## Cycles, rooms, and boards

Run two all-hands Group Chat rooms: one for recon, deliberation, and execution
coordination; one for retrospective and collective self-improvement. Mirror them
with two Kanban boards. Meetings produce decisions, useful challenges, or
improvements -- never compulsory chatter.

Independence first: each persona prepares its current-state assessment
**before** seeing peers.
Review across responsibility boundaries, separate facts from interpretations,
work from a common evidence cutoff, and name missing or stale inputs. Challenge
material assumptions without forcing consensus. Unanimity is a warning sign.

Scheduled jobs initiate work; dependencies and evidence freshness decide
readiness. Offset maintenance to avoid contention. Surface failed runs -- a
silently skipped cycle is worse than a loud failure. An active schedule without
a computable next run is not armed; reject it before accepting the schedule.
Require each job's actual failure delivery target (or an explicit reason it is
silent) and verify where alerts go. Match the worker interpreter, permissions,
and effective timeout to the actual scheduled execution mode, not a manual test.

On the boards: one accountable owner per commitment, a clear completion
condition, durable handoffs, and limited work in progress. Do not create a card
for every tool call. Keep recoverable blockers inside the fleet. **A held card
is addressed to the operator and contains only a precise request needing their
authority, information, consent, or physical action -- with a recommendation and
a clear resumption condition.**

## Decide, then separate

One accountable decision leader decides after hearing the relevant perspectives, and
records the decision, material dissent in the dissenter's own words, the
invalidation condition, and the next action. A deliberate WAIT is a decision
when it carries a reconsideration trigger.

Then keep four things apart: **decision, authorization, execution, and
verification against an external record.** Enforce consequential permissions
through tools and configuration, not prose. Prose is not a permission system.
Deduplicate actions, reconcile an uncertain outcome before retrying it, and keep
a stop mechanism that actually stops things. A pending or unknown external
outcome holds its claim/reservation until reconciled; do not call it a clean
failure or release it on a timeout.

## Collective GRPO

Group-relative learning is the engine: compare alternative approaches on the
same task and the same evidence, judge independently verified outcomes, and hold
absolute acceptance standards. **The best bad result still fails.** Reward
useful results -- not confidence, agreement, verbosity, or activity.

Use actual GRPO when training model weights. Use GRPO-inspired comparison and
selection when improving prose, skills, tools, configuration, and workflows. Do
not confuse operational learning with model training.

Explore through replay, sandboxes, and shadow operation -- never duplicate live
consequences. Compare against the current baseline on held-out cases, preserve
useful diversity, and retain failed experiments so the fleet does not rediscover
them. **Candidates cannot rewrite their own judges, rewards, or authority.**

## Retros

Each persona reflects on its own work and names what deserves sharpening,
tuning, pruning, or replacement: SOUL inclinations, memories, documents, skills,
tools, or configuration. What worked? What failed? What changed? What does
current research suggest? What have we actually proven? What prevents
recurrence?

Peers contribute critique. Separate process quality from eventual outcomes -- a
good decision can lose. Prefer the smallest useful change, including no change.

## The living operating map

Maintain one living, versioned map the fleet is built to resemble. Prose
explains the shape; configuration expresses it; tools enact it; observed
outcomes test it. Distinguish intended, deployed, and observed behavior. Fix
drift rather than rewriting the map to excuse it.

The accepted version governs execution; proposed revisions stay experiments.
Give stewardship to one persona and run retros and automatic learning through a
single change process. Test changes, deploy within authorized boundaries, update
map and implementation together, and keep rollback. Keep each run traceable to
its versions and evidence, and prove recovery works.

**The map guides the fleet; outcomes improve the map; verified changes reshape
the fleet -- without silently changing its mandate.**

## Optional mission-specific examples

The trading-desk cases (arming, reservations, live market prices, and
fail-closed coverage) are illustrative rather than mandatory fleet doctrine.
Read `references/trading-example.md` only for missions where those controls
apply; do not copy a trading venue's rules into a mining or other fleet.

## Detect success-shaped no-ops

After a consequential write, use a native readback and compare the intended
state with the observed state. Distinguish rejected schema/size writes from
stale revisions; do not retry an unchanged refusal as if it were contention.
A successful room send acknowledgment, backend state write, profile creation,
or active schedule label cannot establish client visibility, an operational
persona, or a real next run. Name the actual identity and surface at each hop:
submit versus resume session ids, Desktop `name:` room keys versus hosted-room
`id:` keys, and durable role/class keys versus changeable display labels.
Do not let a renamed label reset a budget or gate. If a fan-out is serial,
re-slice the *remaining* time among remaining members at each step so the last
decision-maker is not silently starved. Each participant may explicitly
`(pass)` when it has no new evidence; never count an empty or unobserved answer
as a pass. If a native operation cannot expose an unambiguous completion and
readback, report unverified rather than success.

## Verification

A fleet built from this doctrine should pass these checks:

1. Every commitment on a board has one owner and a completion condition.
2. Every direction-setting decision record carries dissent, an invalidation
   condition, and a next action.
3. No persona scores its own decisions.
4. A scheduled post is confirmed rendered in its room, not merely acknowledged.
5. A failed run is visible; a skipped cycle is never silent.
6. The map's version matches what is deployed, or the gap is an open item.
7. `MEMORY.md` is under its cap and holds lessons, not procedure.
8. A Holding request reaches the operator through a verified notification
   route, not only the board.
9. Independent monitoring notices when the backend or its schedules stop.
10. Recovery and restoration have been exercised, not assumed.
11. After qualification the fleet runs its production cadence instead of
    going dormant.
12. No domain analysis, experiment, dataset, mission-specific script/tool or
    research publication was admitted while FORMING, including work labeled
    read-only/preparatory or routed through the founder's direct tools.
13. Every persona's recorded work falls within its own responsibility; none was
    absorbed by the founder or a peer. Six configured SOULs alone do not pass.
14. Preflight passed before bounded formation schedules ran; real participation,
    readback, stops and continuation passed before the stage advanced. No
    failure was retried under unchanged conditions.
15. Formation pass came from an independent assessor and the authorized
    acceptance authority, not the founding persona's self-declaration; no
    backend receipt was called Desktop-visible without client observation.

## References

- `references/fleet-seed.md` -- the founding charter, verbatim, sent as the
  founding persona's first message. The authority for everything in this file.
