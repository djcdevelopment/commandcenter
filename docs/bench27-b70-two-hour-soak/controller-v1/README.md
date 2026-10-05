# Duration controller v1 — executable candidate, review pending

This version implements the missing scheduler described in the preserved parent proposal. `soak.spec.json` now contains an executable campaign command; do not launch before independent review. No profile changes or inference have been performed.

The unit and frozen driver/workload hashes are checked. Each iteration uses the reviewed paired driver directly (the same command the existing campaign runner invokes), with a new output directory. Both seats run one work/final conversation concurrently; successful pairs contribute actual2/2 calls. Partial reported calls survive failed or cancelled iterations; unknown counts remain null. Guard refusal propagates from the reviewed paired/thinking drivers. No service or fence handling is duplicated: the existing paired harness owns those and restores afterward.

The first workload run timestamps establish the measured span; completion includes the final pair. Timestamp spans describe workload wall intervals, not continuous GPU busy time or accepted reports. Per-card intervals, idle gaps, global pair gaps and both-card overlap are recorded. Duration-met status explicitly remains pending stability review.

Controller wall maximum is140minutes, cancellation grace40seconds, outer harness maximum150minutes; parent must account for harness setup and restore_by when preflighting. At controller maximum or SIGTERM, only the owned child process group is terminated, with bounded kill escalation. The paired driver retains its cancel propagation. A failed/partial pair ends the controller nonzero. Parent must check engine drain and harness restoration before using the record.

Tests run: `/home/derek/.venvs/hearth-private/bin/python -m unittest hearth.tests.ops.test_bench27_soak` (8pass). Tests cover source-hash refusal, minimum duration restriction, overlap/gap arithmetic including last pair, completed-loop call totals/unique outputs, partial/unknown calls and deadline-owned-child cancellation. Parent owns independent Opus review and any launch decision. Exact source hashes are in MANIFEST.json.
