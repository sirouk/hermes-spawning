# Trading desk examples (optional)

These are mission-specific lessons from a trading desk. They illustrate how
formation and consequence gates can fail; they are **not** required components
of every fleet or authority to trade. Read only if the mission actually needs
orders, exchange inventories, funding certification, or a similar financial
control. The domain-neutral formation and execution rules live in `../SKILL.md`
and `fleet-seed.md`. Adapt tests to the current venue and authority; do not
paste prices, currencies, or permissions into another mission's map.

## Arming a desk for live execution

A mode flag is not capability. Before believing a desk can execute, run the real
code path in a sandbox with a fake fully-armed authority file and confirm an
order actually passes. Interlocks that check mode/scope/expiry and *then* raise
unconditionally are common and deliberate -- they mean a component was never
built. Setting the flag anyway produces a desk that reads ARMED in every listing
and fills nothing, which is strictly worse than DRAFT.

Make arming a tested script with a capability probe that refuses when the code
cannot execute, not a hand-edited JSON file. Hand-editing is how an expiry lands
in the past and every order silently refuses.

When an operator says "no expiry" but the schema requires `expires_at`, encode it
as a far-future date with the intent recorded. Never delete the expiry check to
honour the request literally.

## Funding certification (the double-spend gap)

Document-binding (decision -> risk -> adversarial by hash) proves the paperwork
is coherent. It does **not** prove the money is there. Two orders can each look
affordable against the same balance observation; only a reservation ledger sees
that the first one already spent it.

- Certify a *specific* order (bind by digest), sign the certificate, expire it.
- `free = total - encumbered - outstanding_reservations`, computed inside an
  IMMEDIATE transaction so the check and the write are one atomic step.
- Encumbrance must be measured, never defaulted to zero. If a surface that could
  hold funds is unreadable, refuse to emit that venue -- a false zero authorizes
  double-spending real money.
- Combine overlapping encumbrance surfaces with `max()`, not `sum()`: one
  commitment usually appears on several, and summing double-counts it.
- Release on terminal outcomes; **hold** on pending/unknown. An order that may
  exist remotely must keep its money reserved.
- Sweep only reservations that expired *without ever being submitted*, and record
  submission in the ledger (not just in memory) at the moment before the first
  byte goes out -- otherwise a later sweep frees money for a live order.

## Live market data as evidence

When a funding calculation needs a rate (USDT/USD, any stablecoin peg), never
hardcode parity. A depeg is exactly when the desk's dollar figures stop meaning
what they say, and exactly when venue reads get flaky -- so "assume 1.0 if
unreadable" hides the one event the number exists to catch. Refuse instead.

Guard the rate from both sides: an absurd print (0, negative, 8000) is a broken
venue and must not revalue the book, but a *plausible* depeg (0.97) must pass
through unclamped. State the boundary as a named constant, not a buried literal.
With several sources, take the conservative rate and refuse if they disagree by
more than a plausible spread -- if two venues differ by 5%, one is broken and
nothing can tell which.

Sample the clock AFTER network reads, not before: an `now` captured first makes
every subsequent print look future-dated and trips freshness checks.

## Fail-closed rules that never open

A refusal that can never be satisfied is not safety, it is breakage wearing
safety's clothes. If a venue permanently lists unusual tickers that fail
strict symbol validation, making inventory coverage PARTIAL on every read,
the book becomes uncertifiable
forever and the desk is dead.

Fix it by narrowing on the *cause*, never by relaxing the rule. Ask what the
refusal actually protects against: a skipped row matters only if it could have
held quote currency, and a row skipped because its TICKER is unparseable cannot
be USD/USDT/USDC -- those parse. Admit precisely that cause, keep refusing every
other cause, and pin both halves with tests. Coverage and status are separate
axes: a failed read stays refused regardless of skip reason.
