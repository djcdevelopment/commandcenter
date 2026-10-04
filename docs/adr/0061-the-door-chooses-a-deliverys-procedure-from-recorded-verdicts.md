# 0061 — The door chooses a delivery's procedure from recorded verdicts

**Status:** Accepted (2026-10-04, omen-linux). Derek accepted it by approving the plan that names it
(`~/.claude/plans/staged-soaring-piglet.md`, "The door chooses, and the night can deliver": "A delivery brief with no
procedure named is routed by the door"). Live since the gateway restart at 2026-10-04T06:58Z.

**Companion to:** ADR-0048 (candidates and verdicts), ADR-0054 (delivery is the fifth contract), ADR-0058 (work is
routed by grain), ADR-0059 (the 27B is served for multi-step work), `docs/delivery.md`.

## Context

A delivery can be produced three ways: one schema-constrained call (`one_call`), the carried procedure (`carry`: one
thinking turn writes notes and a report, code carries the report, a second pass attaches quotes) and, since this date,
the items procedure (`items`: code lists items, two fast seats read each, the 27B judges the disputes). Until now the
caller named the way: `procedure="carry"`, `lane="deep"`. The lab's product line says the opposite: a caller submits a
request and the door routes it, or refuses.

The lab-rnd registry holds a capability record for every delivery that got a verdict. No code carried those records to
the door. The night drain could not submit a delivery at all.

## Decision

1. **A delivery submitted without a procedure is routed by the door.** In order: a retry of an existing work reuses its
   recorded procedure; a caller's `max_tokens` or `revise` means one call; a brief that declares `items` takes the items
   procedure; otherwise the door reads a table of recorded verdicts and chooses between `carry` and `one_call`.
2. **The table** (`delivery-procedures.v1`) is generated from the registry (`research/cli.py export-procedures`), per
   backend and task family: accepted and rejected verdicts and distinct briefs per procedure, with record ids. It is a
   tracked and deployed pair checked by `host_config`; the door reads it at each submit.
3. **The rule.** A procedure qualifies when it has accepted verdicts on at least two distinct briefs. The family's counts
   are tried first, then the backend's overall counts. Among qualifying procedures the higher acceptance rate wins; a
   tie goes to the one with more accepted, then to `one_call`. Nothing qualifying, no table, or no entry: `one_call`, as
   before this decision.
4. **A chosen `carry` falls back to one call** where the carried procedure cannot run (not the deep lane, no thinking
   budget on the backend, a `line_reference` form, the context check), and says why. A pinned `carry` still refuses.
5. **Pins stay**: `procedure="one_call"`, `"carry"`, `"items"`. A pin is how a comparison arm is run.
6. **The choice is recorded** in the work manifest: `route.procedure` and `route.procedure_choice` (who chose, at which
   level, the counts, the table's hash, a fallback reason).
7. **The drain may submit a delivery**: a night brief names `delivery_brief` and pins the file's bytes with
   `delivery_brief_sha256`, so an approval of the brief covers the JSON it points to. It names no procedure.

## What the table said, and the first uses

At the restart (06:58Z), `omen-dense-27b`, `code_review`: `carry` 6 accepted, 1 rejected over 3 briefs (2 with an
acceptance); `one_call` 8 accepted, 22 rejected over 5 briefs (1 with an acceptance): `carry` at family level.
`tool_execution`: neither qualifies in the family; overall `carry` 7 and 3 over 4 briefs, `one_call` 11 and 32 over 8:
`carry` at backend level. `omen-vllm` and the AM4 tool seats: nothing qualifies, `one_call`.

First uses (`~/work/delivery-plan/evidence/wave8/RESULT.md`): four briefs submitted with no procedure; the door chose
`carry` for each (three at family level, one at backend level) and recorded the counts and the table's hash. Two were
accepted (backoff `work_2b3f2a82`, perception `work_aa6b13cd`), two rejected (sizing `work_70cda00a`: two line numbers
in the prose; restore `work_db9514bc`: one false statement). With those verdicts imported the table read `carry` 7 and
3, `one_call` 8 and 22 for `code_review`: the preferred procedure's rate fell from 100% to 70% as honest samples came
in, and it is still the choice. At 07:24Z the night drain's timer tick submitted a delivery brief with no seat and no
procedure (`work_a745c074`); the door chose `carry` from that table.

## Limits, stated

- **The door does not explore.** Once a procedure is preferred, the other gets no new evidence unless someone pins it.
- **Counts cross serving profiles.** All carried records were made on the seat's current recipe (ADR-0060); 41 of the
  43 one-call records on the 27B were made on the old one. The evidence that answers do not depend on the attention
  backend is in ADR-0060; the table itself does not know.
- **Repeat runs count as records.** Twelve counted records are byte-identical repeats in five groups; they move the
  rate, not the brief counts the rule qualifies on.
- **Three verdicts can be three samples of one brief**; that is why the rule counts briefs.
- **The lane was not chosen from records** when this was written; see the addition of 2026-10-04 below.
- **Each acceptance rests on one grader and the orchestrator's read.**

## Rollback

Remove `HEARTH_DELIVERY_PROCEDURES` (the drop-in `hearth-production.service.d/delivery-procedures.conf`, tracked and
deployed) and restart the gateway at zero leases: with no table every unpinned delivery is one call again.

## Not covered by the evidence

A second backend with carried records; the rule on a family with a real contest between procedures; a table that
changes between a submit and its retry beyond the recorded-procedure rule; the drain under prod.

## Added 2026-10-04: the lane follows the same table

For a delivery with `lane="auto"`, no pin, no `items`, no caller `max_tokens` and no `revise`: after the lane is picked
by task family and source size, the door asks the table about that lane's backend. When nothing qualifies there and the
deep lane's backend has a qualifying procedure, the delivery takes the deep lane. `route.lane_choice` records from, to,
the level, the counts and the table's hash; a retry keeps its stored lane. When the deep seat is missing or retired the
picked lane keeps the work and `lane_choice.declined` says why. On the day's table every small report brief of a family
other than the two code families moves to the deep seat (carry at backend level: 17 accepted, 10 rejected over 17
briefs); the fast seat and the tool seats have no accepted delivery of any procedure.

Limit: the move does not ask how busy the deep seat is. Capacity is the lease's business; a moved work waits its turn.
