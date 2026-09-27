# ADR-0049: Learning recommends a route; only a recorded decision changes the drafter

- Status: **proposed** (filed by the first `operator learn report`, 2026-09-18; not applied)
- Register: commandcenter `docs/adr` (cite as `commandcenter#0047`)
- Precedent: `commandcenter#0038` — a verdict cites only evidence from the configuration it promotes
- Program: `C:\work\orchestration\local-operator-workspace-20260917`, Gate 6, decision D-116

## Context

Gate 6 introduced `operator learn report` (`hearth/operator/learn.py`). It reads every replayed run
under `runs/operator/` — RUN-STATE.json, the frozen envelope, the proposals, the attempt receipts —
groups the outcomes by route target, host, model, task type and route kind, and writes
`knowledge/operator_learning.json` and `OPERATOR-LEARNING.html`. It excludes `test_mode` runs by
default (D-113). It is byte-stable: no wall clock, inputs named by run id and digest.

Its first report (5 runs, 2026-09-18) produced recommendation **REC-001**:

> For a small read-only engineering task (≤3 input files), the observed fastest route is
> `am4-dense` at 6.109 s mean over 1 run; the drafter's default remains `direct_hearth`.

The ranking it cites (mean end-to-end seconds, operator clock):

| target | mean s | host | model | runs |
| --- | --- | --- | --- | --- |
| `am4-dense` | 6.109 | am4 | am4-dense-27b (Qwen3.8-27B dense, RTX 4070 Ti + RTX 5070) | run-g5-am4-dense-20260918T005630Z |
| `direct_hearth` | 13.656 | omen | qwen3-30b-a3b (dual Arc Pro B70) | run-physical-inventory-20260917T234022Z |
| `mechnet_build` | 17.844 | omen | qwen3-30b-a3b | run-mechnet-inventory-20260917T235927Z |
| `deepagents_hearth` | 29.297 | omen | qwen3-30b-a3b | run-deepagents-inventory-20260917T235035Z |

## Decision (proposed, not taken)

The drafter's default (`draft_route` selecting `direct_hearth` when eligible) does **not** change on
this evidence. A learning report may only recommend. Changing the default requires, in order:

1. a decision recorded in the program repository (this is D-116, status **open**);
2. this ADR accepted, citing only evidence from the configuration it promotes (ADR-0038): the same
   task shape, the same envelope constraints, the same catalog version, more than one run per target;
3. an acceptance gate recorded as a control-plane run (D-110) in which the promoted default drafts,
   validates and executes.

Until all three exist, `route draft` keeps `direct_hearth` as the default and `--target <rung>` is
the only way onto `am4-dense`.

## Why not apply it now

- **One sample is not a regime.** n = 1 per target. The 6.1 s figure is a single run at a 317-token
  prompt; the B70 figures include a 10,182-token pack at G4 that AM4's 4,096-token slot cannot take.
- **The comparison is end-to-end, not decode.** It includes LAN hop, facade, harness and ledger
  overhead. Mechnet's +4 s is receipt work, DeepAgents' +16 s is a tool loop, neither is "slower model".
- **The rung is pin-only by design** (`tags = []`, OPS-01): the door does not route onto it, and
  the drafter should not either until the boundary rows still deferred in `POST-MVP-HARDENING.md`
  (rollback drill, overflow rejection, kill/restart recovery) have evidence.
- **The learning loop's authority is the point.** The first thing it recommends is the first
  thing it must not be allowed to do by itself. This ADR is the record of that refusal.

## Consequences

- `knowledge/operator_learning.json` carries `applied: false` on every recommendation and a
  `promotion_path` naming this route. A reader who finds the drafter changed without D-116 closed and
  this ADR accepted has found a defect.
- REC-002 (rejected first drafts are drafter defects) and REC-003 (no artifact has a verified
  recovery location; D-004/D-114) are filed with REC-001 and follow the same path.
