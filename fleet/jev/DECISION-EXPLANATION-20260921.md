# JEV decision explanation — cycle 20

`fleet-scheduler decision` and `decision --json` are live on FX99. They expose
the saved selection/hold, sanitized candidate judgments, and the exact quality
history digest used. They do not rescore work, spend money, dispatch jobs, or
claim that a high fit score means a correct patch. Human output labels today's
thresholds as **current**, not necessarily the thresholds of an old saved record.

The local-builder experiment **did not improve self-correction**. The CLI was
delivered with Codex corrections and integration; this is assisted delivery.

## What the real lap showed

One source-packed JEV/MechNet task requested `decision_view.py` and supplied an
inline behavioral command using a real saved decision. The builder was told to
write first, execute that command, correct failures within the same job, and
finish only after success. No new test file or separate builder retry was used.

| Stage | Actual result |
| --- | --- |
| JEV | Fit 2.98, confidence 0.98, ambiguity 0.16; dispatched under unchanged gates |
| Quality feedback used | Eleven-task history `33dc5b422652d6ece1aa33bba66bf70f68fa96237adbffdc92fbf5627285bc92` |
| Worker | Five actions: three file writes, two failed command executions |
| Worker completion | Budget exit at 138.8 s; step counter 6, not six executed actions |
| Actual behavioral checking | Neither command reached the assertions: bare `true` raised `NameError` |
| Self-correction | First and final implementations have identical ASTs |
| Independent raw-helper checks | Six failures: three boolean numeric fields, duplicate, empty and non-ASCII IDs |
| Hermes | Static PASS in 25.359 s despite acknowledging the boolean violation |
| Delivery | Codex corrected those conditions; real CLI output verified at 12:27:58 UTC |

The model retyped the provided Python fixture as JSON-style literals inside
`python3 -c`. Later, integrity-checked input artifacts contain the actual
`NameError: name 'true' is not defined` feedback. It nevertheless wrote the same
implementation again. This is evidence against relying on this briefing alone,
not proof that local models cannot use behavioral feedback in another setup.

The inherited farm assay reported 162 existing tests passed and an A. That is
not evidence of coverage of the new helper: the candidate commit adds only
`decision_view.py` and `retro.md`, while direct calls expose the six defects.
Hermes's report is also not acceptance evidence: its opening PASS conflicts with
its own note that boolean numerics violate the specification. No automatic
promotion occurred and no second review was requested to obtain a nicer verdict.

## Delivered code and evidence

`decision_view.py` retains the local implementation except for Codex's strict
numeric checks and unique, nonempty ASCII identifier validation. The CLI and
deployment integration are Codex work. Ten focused comparisons pass on the
delivered helper; the raw candidate fails six. Input immutability and omission
of arbitrary record/row fields were independently checked. These are scoped
checks of this change, not a broad assurance claim.

- [Unedited candidate](evidence/20260921-decision-candidate.json)
- [Execution, worker trace, unedited Hermes review and deployment evidence](evidence/20260921-decision-execution.json)
- Private task, contract and observations: `C:/Users/derek/.fleet-scheduler/decision-cycle20/`

Parent: `br-20260921-122038-3a759f92`.
Child: `br-20260921-122244-a163a601`.
Task: `jev-952e640d8b1e110bf3ed6cab`.
Plan: `hearth-hermes-br-20260921-122244-a163a601-5beaf139`.
Raw candidate commit: `449642c08293a76a2ff8ecd762983dee948cfeca`.

## Time and cost

The lap began 12:15:27 UTC; the first-quarter target was approximately 12:21:35.
The subsequently stated 12:22 first-artifact target was also missed: the task was
queued at 12:22:25 and dispatched at 12:22:45. The first file was observed at
12:23:38, not an exact write timestamp. Setup again consumed too much of the
artifact allowance. Derek was asked about finishing the already-queued attempt
within the existing ceilings; no extra attempt or expanded window followed.

The corrected helper passed its checks at 12:26:24; live CLI delivery was verified
at 12:27:58. Generation ceiling: 12:34. Delivery/restoration ceiling: 12:40 UTC.
The worker and one review were already finished before the live CLI check.

One JEV call: 955 input tokens, 0.265 s, estimated USD 0.000040110. Cumulative:
16 calls; USD 0.000538692 usage estimate plus USD 0.005505024 uncertain
reservations, USD 0.006043716 booked. Reservations are not confirmed bills.
Codex and local hardware costs are not included. No throughput or quality
improvement is inferred from unrelated previous tasks.

## Live scope and restoration

Hash-guarded installation changed only FX99 `fleet/jev/cli.py` and added
`decision_view.py`. CLI before SHA-256:
`ee03d8ab19a9a65e99fd6c7b32d9efcd51fc3503ed91b0be5abfc3b9fd9f31cd`;
after: `dc68c657c710cd7ac15581264c998809d0f718b5081749372552628bf4700f3b`.
Helper SHA-256: `4f39760ab3fb731036f5e7d72c5fb569892658789f9fbdb3a69f6d74de3d1ec3`.
Backup suffix: `.jev-decision-view-cycle20-20260921.backup`.

Fresh CLI invocations use the new command. The running CPU scheduler was not
restarted; its loop and cached module remain unchanged. No gateway restart,
model/topology change, root-branch push, or automatic acceptance occurred.
Hermes's automatic review still used the old default wrapper, not cycle19's
optional packet-only mode. No KV reuse was attempted.

The scheduler reported ready/idle, no active build, no pending review and no GPU
reservation at the live CLI check. Hermes's completion recorded the owned AM4
reviewer unloaded. Final helper ownership and quality-feedback update are recorded
with the closure evidence.

At 12:32:42 UTC, the helper independently confirmed `owner: null` and
`model_resident: false`. The twelve-task curated history is deployed on FX99:
ten reached the worker, one unchanged delivery, eight assisted, one incomplete,
two not_run. History SHA-256:
`a53c5a2582e03f7027b469f25a59126c827c8fc465335ccd4b781963eb89a2df`.
[Feedback/restoration evidence](evidence/20260921-decision-feedback.json).

The saved decision correctly continues to display the **older** eleven-task
digest it actually used. The current history command shows the new digest.
This distinction was observed live without another JEV call. No decision using
the twelve-task history has yet been sampled. This remains operator-curated
feedback, not automatic learning or acceptance.
