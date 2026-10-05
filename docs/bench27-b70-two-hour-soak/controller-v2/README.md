# Soak controller v2 — review candidate, not launched

Supersedes controller-v1 implementation and cleanup claims; older preparation artifacts remain preserved. The runnable spec continues to wrap the unchanged reviewed paired driver on baseline k2/auto-KV 65K. The controller does not install profiles; the existing paired harness **cold-restarts both seats even when drop-in is null**. First pair cold-start measurements remain visible.

The controller owns its child session and propagates SIGTERM. Both deadline and finally cleanup use the same25-second grace and then SIGKILL, before the outer harness's30-second kill window. Vanished process groups are tolerated. The frozen helper paths/hashes are unchanged. Tests exercise the actual installed SIGTERM callback, escalation ordering, process-exit race, partial counts and model-failure exit4.

`calls` stays null for the harness if a pair's counts are unknown. `known_calls` independently preserves earlier confirmed totals, and `unknown_pairs` identifies uncertainty. Known counts survive missing run metadata. Failure remains nonzero; model failure preserves code4/model_failed.

The first/last complete run timestamps are whole seconds. Successful duration now requires at least7201 recorded seconds for a requested7200, and the resolution is explicit. Spans include workload startup/inspection/admission overhead and are not GPU utilization. Per-card idle, global gaps and overlap remain recorded; parent must evaluate actual counter/thermal coverage. A duration status alone is not accepted stability or report capability.

Controller max is8400seconds plus25seconds cleanup. Harness max150minutes applies to campaign phase; baseline cold-restart setup precedes it. Parent must check the actual campaign start time and remaining restore_by budget rather than assuming setup consumes the same budget or allowing a post23:00 rollover into night. Keep real restoration margin. Deep/drain/local-work traffic must be paused by its owner because the dense lane is not fully protected by the pool fence; foreign calls contaminate the result. Guard/fence/restore remain harness-owned. No live call or pause is performed here.

Validation: `/home/derek/.venvs/hearth-private/bin/python -m unittest hearth.tests.ops.test_bench27_soak` —14passed. Actual prepared unit verifies against unchanged frozen helper/workload hashes. Reviewed source SHA and executable spec SHA are refreshed in MANIFEST.json. Parent owns independent review and launch; do not infer approval from this file.
