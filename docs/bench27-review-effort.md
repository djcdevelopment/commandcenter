# Offline review effort calculator — schema v1 proposal

`python3 tools/ops/bench27_review_effort.py /ABS/intervals.json` reads one JSON input and prints its calculation to stdout. It creates no files, calls no services and handles no credentials. Independent review is required before using its output as campaign evidence. This proposal does not alter the human effort recorder or any approval gate.

Frozen input schema `bench27-review-effort.v1`:

```json
{
  "schema": "bench27-review-effort.v1",
  "cohorts": [
    {"cohort": "cap1", "kind": "capacity", "complete": false,
     "evidence": "coverage-attestation.json"}
  ],
  "deliveries": [
    {"work_id": "work-example", "cohort": "cap1", "status": "accepted",
     "evidence": "work-example/VERDICT.json"}
  ],
  "intervals": [
    {"actor": "codex", "session": "parent-cap1", "work_id": "work-example",
     "cohort": "cap1", "activity": "review", "start": "2026-10-05T12:00:00Z",
     "end": "2026-10-05T12:02:00Z", "evidence": "review-interval-log.json"},
    {"actor": "opus", "session": "grader-example", "work_id": "work-example",
     "cohort": "cap1", "activity": "independent grading", "start": "2026-10-05T12:01:00Z",
     "end": "2026-10-05T12:03:00Z", "evidence": "grader-example/result.json"}
  ]
}
```

Cohort kind is qualification or capacity; use separate IDs for qualification and cap1/cap2/cap3. Work IDs are globally unique in the input, terminal status is accepted/rejected/failed, and every interval references its delivery's cohort. Actor is codex or opus. Session identifies a worker session scoped to one cohort: split a parent session identifier by cohort rather than carrying it across qualification/cap boundaries. Parent active intervals assigned to different cohorts must not overlap in clock time; changing a session ID does not make double-counting legitimate. The owner must check this across the separate cohort records. Activity is a nonempty description (review, diagnosis, repair, or re-review). UTC timestamps require an explicit zero offset and positive duration. Every cohort, delivery and interval needs a nonempty evidence reference. References are retained as authored provenance; the calculator does not open them or authenticate the underlying verdicts or clocks.

`complete` is a required owner attestation that all relevant review/repair intervals and terminal deliveries for that cohort are represented. Complete requires a nonempty delivery list and interval coverage for every delivery; this is a consistency check, not proof that an author omitted nothing. Do not declare complete while a delivery/review is pending, a reviewer is missing, or historical timing must be guessed. Use complete=false to retain partial observations; missing delivery coverage is listed, rates are marked partial_observation. A partial rate is neither an upper nor a lower bound: both missing effort and missing accepted deliveries can change it. Zero accepted rates are always null/undefined_zero_accepted. A failed delivery with no measured review interval cannot support complete coverage; do not invent a positive interval.

Intervals for Codex are **agent effort**: actual active review/repair windows, excluding waits and unrelated work. Opus intervals are **automated review duration**: actual invocation-to-completion wall windows, not assertions of continuously active reasoning or human attention. Include rejected/failed deliveries and repair re-reviews in the numerator. Record repair execution in separate input rows with its own activity when measured; activity rows are retained in the input but the calculator does not output an activity breakdown. Never infer a repair breakdown from session totals; never substitute model generation runtime. Overlaps within each actor+session are merged once. Session minutes sum to per-actor minutes and worker_sum_minutes. `actor_minutes_per_accepted` reports each actor separately (null when accepted=0). `worker_sum_basis` explicitly labels the combined value as a heterogeneous sum of Codex active agent effort and Opus invocation wall duration; it is not a uniform effort measure; concurrent independent sessions legitimately add worker time. Separately, union_elapsed_minutes merges all observed intervals across actors within that cohort. The two measures are never added together. Rates divide each separate numerator by accepted deliveries; they do not imply manual-work savings.

Human review and manual baseline default to `{status: unknown, minutes: null}`. Each cohort may optionally provide `human_review` and/or `manual_baseline` as `{"minutes": NONNEGATIVE_FINITE_NUMBER, "evidence": "actual-measurement-reference"}` only when actually measured. These values are reported separately, never inferred from Codex/Opus intervals and never mixed into their numerator. Comparability and evidence remain frontier/human review responsibilities. `economic_benefit` is always `not_assessed`, even when such measurements are supplied; the calculator does not declare H-1 green or compare incompatible regimes.

Validation rejects malformed timestamps, non-UTC or reversed/equal intervals, missing evidence, unknown work/cohorts, duplicate deliveries/cohorts, cross-cohort sessions, missing completeness declarations, falsely complete uncovered delivery lists and invalid optional measured values. Output retains cohort boundaries, actor/session labels, coverage evidence, counts, rates and unknowns. Preserve the input alongside output to audit work/activity/evidence rows. No aggregate qualification-plus-cap economics is generated.

Offline tests cover overlapping same-session intervals versus concurrent workers, inclusion of rejected and failed effort, zero accepted and partial rates, invalid inputs, separate cohorts, and evidenced optional manual values without an economic verdict. Run:

```bash
python3 -m unittest tools.ops.test_bench27_review_effort -v
```
