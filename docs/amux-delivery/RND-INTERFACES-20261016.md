# HEARTH interfaces R&D slice — 2026-10-16

Receipt: `br-20261016-065248-7c6bf7ea`; parent task attribution `cx-adhoc`.
Status: BUILT NOT DEPLOYED. No service restarts, configuration changes or inference dispatches in this slice.

Observed defects: `plan_execution(inference.deliberate, task_family=...)` bypassed the family regime that submission applies. Direct callers had no authenticated own-execution inbox, while delegated listing reversed only the oldest 10,000 global jobs and could hide recent work. Delegated submitted timestamps read a nonexistent `submitted_at` field.

Implemented:

- Preview accepts optional `thinking` and `procedure`. Both preview and submit share deliberate family/policy normalization and cap validation. Family seat order is unchanged. Caller thinking/procedure overrides retain the existing paired behavior: explicitly naming thinking suppresses the family procedure and runs one call unless a procedure is also named. Procedure-only without a boolean thinking control is now rejected before dispatch rather than failing later in the worker.
- Preview returns effective thinking/procedure, policy, existing selected provider/model/routing information, `regime_provenance`, and `planning_context`. Accepted request desired state records the same provenance and a fresh context snapshot. The latter contains observed time, active configuration name/source and environment name/source/expiry/missing status. These observations do not reserve capacity or promise that configuration remains unchanged before dispatch.
- `list_my_executions(task_id=None, status=None, limit=20, before_sequence=None)` returns `{executions: [...], next_before_sequence: int|null}`. Limit is 1–100. Ownership is always exactly the authenticated `hearth_caller` principal, including unrestricted callers. Filters apply before the SQL limit; request-event sequences provide an immutable newest-first exclusive cursor. A fresh first page finds new submissions. Each row exposes canonical status/reason, timestamps, operation/task and artifact references, never prompt bodies.
- Existing delegated listing keeps its arguments, list response and adapter ownership checks; it now uses the same principal-filtered query. No new state database, canonical-event rewrite or migration is needed. The new tool uses the existing execution capability and is categorized as a control-plane call.

Focused validation (temporary ledger/backend fixtures, no inference):

```sh
/home/derek/.venvs/hearth-private/bin/python -m unittest \
  hearth.tests.execution.test_service.DeliberateOperationTest.test_family_preview_matches_submitted_regime_and_explicit_controls \
  hearth.tests.execution.test_service.DeliberateOperationTest.test_admission_refusals \
  hearth.tests.execution.test_service.DeliberateOperationTest.test_recovery_fails_a_running_turn_instead_of_replaying_it \
  hearth.tests.toolsurface.test_execution_control.ExecutionControlToolsTest
```

Result: 10 checks passed in 0.170s; command output captured at `/tmp/hearth-rnd-interfaces-focused-20261016.txt`. Checks cover family think-then-emit defaults, explicit controls and budget, thinking-only override, matching preview/accepted request, cap refusal, real scratch-ledger pagination/filtering, foreign direct/delegated exclusion including unrestricted own-list calls, missing identity, and timestamp output. No new test files were added.

Restart edge retained: an interrupted running `inference.deliberate` execution is marked failed and is not automatically replayed. It requires a new submission with a new idempotency key if the previous request used one. Ordinary generation recovery remains unchanged. Listing/fetching a job does not resolve or acknowledge it.

Next physical action: parent integrates this commit with the staged delivery increment, then performs its sole-owner gateway deployment. After deployment call `plan_execution(operation="inference.deliberate", task_family="code_review", prompt_bytes=100)` and compare a matching authorized submission's desired regime and invocation. Use `list_my_executions(limit=1)` under the caller that submitted it, then its returned cursor. Live MCP registration, real inference agreement and cross-session caller credentials remain UNVERIFIED here; they require the parent deployment and caller integration. The catalog renderer may need refreshing after the merged capability set is final.

No local offload was made: this slice is coupled authorization, ledger and execution-path integration; briefing and reviewing separate generated fragments would not reduce its integration cost.
