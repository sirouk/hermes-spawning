---
name: fleet-organism-design
description: Design a self-optimizing multi-persona Hermes fleet that learns from its own results.
version: 2.0.0
author: Chris (sirouk), Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [fleet, personas, ooda, grpo, retro, kanban, group-chat, governance, mission-handoff]
    related_skills: [hermes-group-chat-delivery, fleet-convergence-learning]
---

# Fleet Organism Design

Doctrine for shaping a multi-persona Hermes fleet into one organism: how
responsibility, authority, and learning are arranged so the fleet improves from
its own results.

This is the **design** layer -- what shape the fleet should have. It is not a
repair manual for a job that already exists.

Use `fleet-convergence-learning` for the sparse episode/pattern ledger and
unchanged-failure retry guard described here.

The founding charter is `references/fleet-seed.md`, reproduced verbatim. When
this file and the seed disagree, the seed wins. Read it first; everything below
is how to enact it on Hermes. The doctrine is domain-neutral: the mission
supplied at handoff decides what the fleet works on.

## When to Use

- Founding a new fleet: sending the seed and carrying the mission handoff
  through qualification
- Standing up or restructuring a multi-persona fleet that runs on a schedule
- Deciding how a cycle advances through native tools without producing internal
  artifacts, and what a retro is allowed to change
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

1. **Study before asking.** Record the seed's immutable source locator and
   provenance, study the mission and the existing environment through their
   authoritative tools, and verify installed Hermes versions and capabilities.
   Do not copy the seed or observations into a working packet. No persona,
   capability, or schedule changes yet.
2. **Ask only what matters.** One consolidated, numbered set of questions the
   evidence leaves open, each with a recommended option, alternatives, and room
   for a free-form answer -- or a short statement that none remain.
3. **Formation gate.** Set the mission contract and Fleet Operating Map v0 in
   the fleet's configuration and fix the acceptance contract. Preflight the
   environment -- native tool calls,
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
For Desktop rooms, the seat `connectionId` is owned by the operator's Desktop
registry, **not** by the spawned host, Tailscale name or profile label. Before
creating or seating either all-hands room, obtain its actual Desktop source id
or have Desktop's Manage members picker seat live roster rows; never synthesize
the id from a hostname.
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
client or mark visibility unknown. Before qualification, use the read-only,
fail-closed `lib/fleet_doctor.py --verified-connection-id ID
--require-room-connection ROOM_KEY=ID` check on each target gateway and verify
all seats against the operator's actual Desktop source; this is a projection
check, not a native action guard. Have the operator confirm both rooms under
the intended gateway filter and witness an attributed member reply. Exercise
handoffs through native Kanban transitions and room turns; file-first handoffs
are not part of this fleet model. An independent assessor verifies the native
events and observed outcome; only the operator or the acceptance authority designated in the
operator-approved contract may approve the stage. Passing formation admits
mission buildout, not production release. Where available, enforce the stage at
all mission admission/action paths, including native Kanban, cron, direct
founder and one-shot execution; record uncovered paths instead of claiming
that this prose enforces a security boundary. Material context, roster,
permission, tool, or room-path changes invalidate the relevant evidence.

The seed's `FOR THIS FIRST MESSAGE ONLY` section and the `I'm ready.`
handshake are one-time. Once the map exists, the enduring doctrine lives in
configuration and focused skills. Preserve the seed's source locator and
version; do not create another archival copy.

## Driving formation turns

Formation is driven from the host, not left to wander. These rules come from
live fleet operation and are doctrine, not suggestion.

1. One conversational driver per session at a time. A resumed session that
   prints "Another Hermes process is using this session" does not become
   available later: the CLI re-checks for about 30 minutes and then refuses
   the turn. Abort the driver at the first copy of that banner, free the
   session, then relaunch. Never queue a second driver against a busy one.
2. Killing the host-side client does not stop the turn. `docker exec` chat
   processes keep running after the client disconnects. Check for leftovers
   with `docker exec <container> ps -eo pid,args` before relaunching; kill
   stragglers with `docker exec --privileged -u 0 <container> kill -9 <pids>`
   (a plain docker exec kill is denied by the container's user namespace).
3. Formation mutates in small, verifiable batches. An unbounded "go form
   everything" turn stalls in meta-analysis: the agent reads hook code,
   debates syntax, and produces documents about building instead of building.
   Drive mutations as short atomic CLI calls (one board, one card, one job),
   verify each one before the next, and let conversational turns only
   interpret and report. A turn that ends analysis-complete but
   mutation-empty twice in a row is a stall: decompose, do not resume.
4. Verify success by bytes, not by echo. A mutating CLI can return success
   while the store stays unchanged, and read-side CLIs can miss journals the
   writer saw. After each mutation, read the raw store (for example
   `cron/jobs.json`, the board store, the profile store) and diff bytes;
   `hermes cron list` alone is not evidence of persistence.
5. CLI semantics worth memorizing: kanban takes `--board` BEFORE the action
   and the card title is positional (no `--title` flag); cron `--repeat` is
   an integer count and the cron expression is the positional argument;
   `--no-agent` requires `--script`, whose path resolves relative to
   `~/.hermes/scripts`; `--workdir` must be an existing directory and does
   not choose the profile store, so per-persona cron jobs need profile
   scoping at creation; `--query-file <path>` resolves INSIDE the container,
   so send prompts through stdin instead: `docker exec -i ... --query-file -`
   with the file piped in.

`scripts/fleet-turn.py` on the host encodes rules 1, 2, and 5's stdin rule
and refuses to launch against a busy session.

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

## Hermes surface fitment and operational state

Use every Hermes surface for the kind of state it is good at. The rule is not
"nothing on disk"; it is "no paperwork as orchestration."

| Concern | Native home |
|---|---|
| Short-lived thought, tool results, and speech | Current session/token context; discard after the next state has what it needs |
| Present truth and external effects | Authoritative connector or tool, followed by native readback |
| Reusable method | Focused skill and, where necessary, one tested specialty tool |
| Authority, topology, budgets, and guards | Versioned Hermes/fleet configuration |
| Time and event initiation | Native schedules and bounded triggers |
| Commitments, ownership, dependencies, state, and handoff | Native Kanban card and transition metadata |
| Multi-person challenge and synthesis | Group Chat room turn or triggered meeting |
| Durable experience | Sparse convergence ledger plus tiny promoted pattern cache |
| Human-readable durable output | Only canonical doctrine/contracts or an operator-requested mission deliverable |

Do not store private chain-of-thought. Collapse transient cognition into the
smallest decision, action, source locator, or conditional pattern another state
actually needs.

Map the mission onto an observable state machine. The normal spine is
`READY/WAITING -> OBSERVING -> ORIENTING -> DECIDING -> AUTHORIZING -> ACTING
-> VERIFYING -> LEARNING -> READY/WAITING`; `BLOCKED`, operator-only `HOLDING`,
and `STOPPED` are explicit side states. A native event plus a configured guard
moves the work; a filename never does. It is valid to collapse adjacent states
for a cheap reversible task, but decision, authority, external action, and
verification remain distinguishable for consequential work.

Fan specialists out independently when diversity is worth its cost. Give
comparable candidates the same task, evidence cutoff, constraints, and reward
dimensions, and prevent peer answers from contaminating independent
observation. Fan in only the decision-relevant result and its source locator.
Call a special meeting when configured thresholds detect material disagreement,
ambiguity, consequence, cross-role dependency, or invalidated assumptions.
Routine owned actions do not need a room ritual.

## Anti-theatre, concretely

The hard default is: **internal artifacts are forbidden.** No cycle reports,
role packets, evidence bundles, handoff files, close files, checkpoint
Markdown, or prose whose only reader is another bot. A durable write is allowed
only when it is Hermes configuration, a reusable skill or tool, an
operator-requested mission deliverable, native Kanban/room/runtime state, or a
minimal convergence-ledger event required for audit, recovery, deduplication,
or learning. If a line or permitted write does not change an outcome, it is
cruft. Applied ruthlessly:

- No ceremonial acknowledgments and no status spam. "Working on it" is noise.
- A send acknowledgment is not delivery. Read back the write on its actual
  surface, distinguish rejected/schema/size writes from stale CAS, and verify
  the message reached the room and settled. Backend readback cannot prove it
  renders in Desktop; observe the client separately or mark visibility unknown.
- Do not document the same instruction in three places. Keep one canonical
  source and point at it.
- A failure becomes one structured episode, a retrievable conditional pattern,
  and when useful a corrective drill -- not a report or promise to do better.
- Evidence is retained by the native system that performed the work. Store a
  source/event locator, not a copy made to prove diligence.
- Hash or checksum only what someone will actually verify against. A digest
  nobody checks is decoration.
- One flow record per commitment, on its existing Kanban card, plus at most one
  linked learning event when the outcome can improve future behavior -- not a
  draft, review, disposition, and summary of the same small step.
- Say what happened. Do not string "not X, not Y, not Z" caveats onto a
  result; limitations that matter go on the card once.

## Where knowledge lives

Each store has a job. Putting content in the wrong one is a common and
expensive mistake.

- **SOUL.md** -- the persona's inclinations, judgment style, purpose, and
  boundaries. Identity, not procedure. Every root and named persona carries
  the Fleet Seed's marker-delimited `PURPOSEFUL CONFIDENCE` core verbatim;
  role-specific identity remains distinct around it. Refine deliberately, on
  evidence, and use the sync tool rather than allowing copies to drift.
- **MEMORY.md** -- a tiny active cache of compact, verified patterns. It is capped by
  `memory.memory_char_limit` and rides in the prompt on every turn. Over the
  cap, entries still load but every later addition is refused. Keep it short by
  intent: applicability conditions and provenance preserved, conflicts
  consolidated, stale guidance removed. It is not a journal, transcript, run
  log, or index of reports.
- **Convergence ledger** -- structured material attempts, observed outcomes,
  source/event locators, causal failure signatures, and conditional patterns.
  Retrieve only the few applicable patterns under a strict context budget;
  never load the ledger wholesale.
- **Documents and files** -- not a workflow surface. Keep only canonical human
  doctrine/contracts, operator-supplied source material, and actual mission
  deliverables. Never use them for bot handoffs, cycle state, readiness proof,
  or retros.
- **Skills** -- guided procedures with prerequisites, expected results,
  verification, and failure handling. This file is one.
- **Tools** -- native first. Build a specialty tool for a genuine gap, never a
  parallel orchestration system. Tools need clear inputs and outputs, bounded
  execution, meaningful errors, and a check that detects breakage before
  consequential use.

### Full SOUL inheritance and dense projections

The safe default is the full marker-delimited Fleet Seed section, installed by
`lib/sync_soul_core.py`. Its size is acceptable: SOUL is the organism's active
self-image, and losing a qualifier costs more than carrying the words.

A smaller machine-facing projection may replace that inherited section only
after a deliberate, version-bound qualification. The recommended method is
[`deep-distill` machine mode](https://github.com/sirouk/deep-distill): inventory
every atomic directive, condition, exception, prohibition, permission,
priority, literal, role boundary, and state transition; generate several ASCII
candidates; have independent readers reconstruct the directives from the
candidate alone; compare both directions against the source; patch until
recovery is complete; then tighten only while the candidate still passes and
is smaller under both tokenizers.

For fleet admission, “lossless” means **100% recovery of the explicit directive
inventory with no weakened scope, collapsed qualifier, contradiction, invented
authority, or missing literal**. It is strong practical evidence of functional
equivalence, not a mathematical claim that every model will behave identically.
The source Fleet Seed remains canonical and human-auditable. Bind a qualified
projection to the exact source version/hash in configuration, record one
curator-independent qualification event in the convergence ledger, and retain
no staging workspace. Any source change, missing certificate, failed blind
reconstruction, or token-gate regression invalidates the projection and falls
back to full verbatim inheritance. A candidate cannot certify itself.

## Cycles, rooms, and boards

Run two all-hands Group Chat rooms: one for recon, deliberation, and execution
coordination; one for retrospective and collective self-improvement. Mirror them
with two Kanban boards. Meetings produce decisions, useful challenges, or
improvements -- never compulsory chatter.

**Room-seat qualification (both rooms, every fleet):** Read
`hermes-bot-roster-and-rooms` and its registry-surgery reference before any
server-side seat write. Confirm the intended *Desktop* rooms, keys and current
operator-client roster. A remote gateway's ID is client-owned; a guessed or
`local` seat can leave a room visible under Any but hidden by the remote gateway
filter while duplicate ghost members fill the six-seat picker. Back up the
client and every gateway projection before any repair; do not recreate rooms,
wipe a container, change logs, or generate IDs from gateway names. If removal
of known-bad seats is necessary, use a narrow authenticated CAS change that
preserves room keys, posts, tombstones and unrelated rooms, then reseat from
Desktop's live picker. Compare fresh revisions on *all* projections; a tie
unions stale seats and a lower revision loses. Read back each projection, then
ask the operator to verify both rooms under the correct filter and test a real
member reply. If any room disappears from a gateway projection later, fail
the gate and investigate; a one-time PASS was not stable evidence. Check
`fleet_doctor.py`'s `desktop_room_capacity` warning: the coordinated source
policy allows a 192,000-byte Desktop projection with a 262,144-character
incoming gateway `ui_meta` cap. Old clients still have a 48,000-byte envelope;
old gateways still have a 65,536-character cap. An unverified client, including
an operator's Mac, must be treated as old until its installed version is
checked. Desktop may omit whole rooms without tombstones. Back up the owner's
full local state and every gateway projection, upgrade/verify gateway first,
then upgrade/verify Desktop. For rollback, stop large publishes and restore
old-client headroom before reverting the gateway; do not erase another fleet's
transcript, delete missing rooms or auto-reconcile. Fix the owner/scope/capacity
path before qualifying two new rooms.

Independence first: each persona observes its authoritative sources **before**
seeing peers, then contributes only the decision-relevant finding, source, and
observation time directly to the room. Review across responsibility boundaries,
separate facts from interpretations, work from a common evidence cutoff, and
name missing or stale inputs. Challenge material assumptions without forcing
consensus. Unanimity is a warning sign.

Scheduled jobs initiate bounded work through native tools. Each run observes
live authoritative sources, retrieves a few applicable convergence patterns,
and advances its Kanban or room responsibility. Ordinary tool results are
ephemeral; native events and the minimal convergence ledger retain only what
matters. Dependencies and evidence freshness decide readiness. Offset
maintenance to avoid contention. Surface failed runs -- a silently skipped
cycle is worse than a loud failure. An active schedule without a computable
next run is not armed; reject it before accepting the schedule.
Require each job's actual failure delivery target (or an explicit reason it is
silent) and verify where alerts go. Match the worker interpreter, permissions,
and effective timeout to the actual scheduled execution mode, not a manual test.

The boards are the system of record for process flow: commitment, owner,
dependencies, current state, blockers, review, and bot-to-bot handoff. Put the
smallest decision-relevant summary and structured metadata on the transition;
never attach a report merely to complete a handoff. Use one accountable owner
per commitment, a clear completion condition, and limited work in progress. Do
not create a card for every tool call. Keep recoverable blockers inside the
fleet. **A held card
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
useful diversity, and retain failed experiments as compact structured episodes
so the fleet does not rediscover them. Group comparable episodes by goal,
conditions, evidence cutoff, and reward dimensions. Promote repeated evidence
into a conditional pattern: *when these conditions hold, prefer or avoid this
move because these outcomes occurred*. Preserve supporting and contradicting
episode IDs, confidence, freshness, environment/tool versions, and authority.
Exact causal failure signatures are checked before retry; an unchanged known
failure is denied until a relevant condition changes. **Candidates cannot
rewrite their own judges, rewards, patterns, or authority.** The actor that
produced an episode cannot alone promote it into doctrine.

## Retros

Each persona reflects on observed outcomes and names what deserves sharpening,
tuning, pruning, or replacement: SOUL inclinations, memories, skills, tools, or
configuration. What worked? What failed? What changed? What does current
research suggest? What have we actually proven? What prevents recurrence?

Peers contribute critique. Separate process quality from eventual outcomes -- a
good decision can lose. Persist no retro essay. Record a novel episode or
update a converged pattern only when it changes future behavior; otherwise
close the loop with no write. Prefer the smallest useful change, including no
change.

## The living operating map

Maintain one living, versioned map the fleet is built to resemble. Keep the
machine-operational map in configuration; use concise prose only where human
judgment needs explanation. Configuration expresses the shape; tools enact it;
observed outcomes test it. Distinguish intended, deployed, and observed
behavior. Fix drift rather than rewriting the map to excuse it.

The accepted version governs execution; proposed revisions stay experiments.
Give stewardship to one persona and run retros and automatic learning through a
single change process. Test changes, deploy within authorized boundaries, update
map and implementation together, and keep rollback. Keep each material episode
traceable to its versions and native evidence locators, and prove recovery
works.

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
16. Bounded readiness drills and production cycles create zero internal report,
    handoff, close, checkpoint, or retro files; permitted mission deliverables
    are explicitly attributable to an operator request.
17. Every bot-to-bot handoff is a native Kanban transition or room turn, and
    completion is derived from native state plus observed outcome -- never from
    the existence of a filename.
18. Before a consequential retry, the convergence guard checks the causal
    failure signature. An unchanged known failure is denied, while recall loads
    only a bounded set of applicable, source-linked patterns.

## References

- `references/fleet-seed.md` -- the founding charter, verbatim, sent as the
  founding persona's first message. The authority for everything in this file.
