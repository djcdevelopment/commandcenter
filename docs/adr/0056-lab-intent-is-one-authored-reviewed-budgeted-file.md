# 0056 — The lab's intent is one authored, reviewed, budgeted file that every agent loads

**Status:** Proposed (2026-10-02, omen-linux; plan `~/work/intent-plan/`, briefs 1–13). It becomes Accepted when brief
13's full probe record exists in `~/work/lab/probe/records/` (every class at its bar or recorded `unavailable` with a
reason) and Derek accepts.

**Companion to:** ADR-0048 (local-model work is a candidate until a frontier verdict), ADR-0053 (the environment is
the policy axis), lab-rnd ADR 0007 (acceptance is invoked and recomputed from artifacts, not read back), manifest
ADR-011 (consumer contract: `framework load --json`, additive-only, consumers ignore unknown fields).

## Context

The lab's purpose was written in nine places and reachable from none of them by default: six lab-rnd paraphrases
(VISION, INTENT, STRATEGY, README, `plans/00-mission.md`, AGENTS.md), the flash README's product framing, the
manifest's single optional `intent` string per constellation (no lab above the 12), and Derek's words in site and
memory. `~/.claude/CLAUDE.md` carried only the offload block; `~/work/AGENTS.md` opened with the PCIe investigation;
Codex had no global `~/.codex/AGENTS.md`; a local seat sees only its brief. Per-task objectives exist (`ct work open
--objective`, `submit_local_work(intent, acceptance_criteria)`, night-brief `CCMETA`, registry `G-ecosystem` and
`G-daily-capability`) but none joined to a lab goal. A cold agent landed on a paraphrase and oriented from it.

## Decision

1. **One canonical home.** `~/work/lab/intent.yaml`, schema `lab-intent/v1`, in the `~/work/lab` git repo on `master`
   (already inside `HEARTH_SCOPE`; no scope change). Pydantic models use `extra="allow"`; v1 is additive-only and
   consumers ignore unknown fields, as manifest ADR-011. `lab-intent load --json` is the consumer entry.
2. **Three rendered tiers, each with a budget.** Hot `LAB-INTENT.md` (≤ 1,200 tokens) for Claude (`@import` span in
   `~/.claude/CLAUDE.md`), Codex (span in `~/.codex/AGENTS.md`) and the 27B (`files=[…]`); capsule
   `LAB-INTENT.capsule.txt` (≤ 220) carried inline by 8B/7B seats, `submit_local_work` and night briefs;
   `LAB-INTENT.html` for Derek; plus `DEPTH.md`. Renders are never hand-edited. Raising a budget is Derek's call.
3. **Rules by pointer only.** `rules[]` names paths (ADR-0048, the offload block's flash source doc, `~/work/AGENTS.md`,
   lab-rnd AGENTS.md, ADR-0053); no rule text is copied. Every pointer lives inside `HEARTH_SCOPE` so the door can pack
   it for a seat that cannot follow pointers.
4. **One `focus`, never a list.** It carries acceptance criteria, blockers and a next action; when the goal exists in
   the lab-rnd registry (`from_registry: true`) its criteria are read from `snapshot.json`, never typed twice. A paused
   or done focus fails `check`.
5. **Joins are an additive `goal_id`.** `ct work open --goal`, a `goal_id` key in night-brief `CCMETA`, a `[goal:G-…]`
   first line in local-work `intent`, a `[goal:G-…]` build-request title prefix. Nothing breaks without one; an absent
   id is at most a warning row.
6. **`lab-intent check` is the freshness gate.** Read-only, one row per rule (schema, 14-day review, focus live, goals,
   depth, drift, budgets, wiring, lab registry, probed), exit 0/1/2; it runs from `tools/ops/host_config.py --check`
   through a `MANIFEST` row for `~/bin/lab-intent`.
7. **The probe is the acceptance gate for every revision.** Eight questions across model classes (frontier, builder,
   local deep, local tool), each at its bar; it tests the spec, not the models. A disagreement is a spec gap: sharpen
   an existing line, bump the revision, re-probe. An unreachable local seat is recorded `unavailable`, never retried
   on cloud.
8. **Gated fields need Derek.** Vision, objective, focus and `constraints.hard` change only in a revision with
   `reviewed_by: derek`; frontier drafts, Derek accepts.
9. **As recorded (D5, D6).** No per-turn hook injection: the CLAUDE.md `@import` span is the only Claude load path.
   No autonomy matrix in the hot file: autonomy and stop rules stay in the documents `rules[]` points at.

## Consequences

- One place to point every entry at: Claude, Codex, the 27B, the small seats and Derek's view all render from the
  same YAML, and a cold agent in any cwd orients the same way.
- The mechsuit-plan question open since 2026-09-30 ("what is the 30-day outward result?") now has an owner field:
  `focus` (D2: the delivery contract end-to-end).
- Stale documents become pointers instead of competitors: the six lab-rnd files, the flash README and AGENTS.md carry
  a pointer or supersede note and stay in `DEPTH.md` as lineage.
- Costs: a review every 14 days or `check` fails; a probe per revision (the 27B at ~10 tok/s is the long pole); the
  renders must be re-run after every YAML edit, and drift fails `check` until they are.
- Accepted gap: a mid-session focus change is seen next session or on re-read (D5).
- Accepted gap: Windows sessions cannot see `~/work/lab` until a manifest-side pointer exists (the Windows sync tool
  is node; the durable fix is Windows-side, later).
- Accepted gap: rev 1's hot render is 6,278 bytes (~1,794 tokens at bytes ÷ 3.5) against 1,200; the budget may need
  raising, or the hot flags trimming, once the seat's real tokenizer count is measured.
