# Compare matching work, not just route names

The [corrected historical operator report](reports/operator-learning-20260921-cycle18.html)
and [full machine-readable JSON](reports/operator-learning-20260921-cycle18.json)
preserve all seven on-disk historical runs. Six completed runs have six different
recorded workloads; none establishes a cross-route comparison. This is not live
capacity or permission to switch hardware.

The old frozen report contained five runs and named AM4 Dense the fastest route.
Its ranking combined different requested work, including a roughly10k-token
investigation and a317-token run. The same five original rows were verified
unchanged, then replayed with workload identities: the unsupported route preference
disappeared. Two later historical records are included separately in the new
seven-record report. The initial assumption that both datasets had five rows
failed; that difference is preserved in the evidence, not silently ignored.

`hearth/operator/workload.py` hashes recorded intent, criteria, inputs,
classification and context limit. `learn.py` now compares only matching keys,
completed/history-verified/reconstructable non-test records, finite positive
durations and at least two distinct targets. Different groups remain separate.
Mixed by-target aggregates are explicitly not rankings. A labeled controlled
example verifies that a matching pair still compares and an unrelated faster
row does not enter that ranking. This is not a new hardware measurement.

Matching metadata does not verify source contents, equal answer quality or
current readiness. Recommendations still apply nothing. This source was executed
in the JEV worktree only; the original operator-program worktree/report, live
JEV route, catalogs, gateways and hardware configuration remain unchanged.

## Local work and review

JEV selected `jev-9ecc042aafb4e2717e7dd093`: fit2.99/confidence0.99/ambiguity0.12.
OMEN wrote the helper on its first898-token response in13.094s, ran the exact
`py_compile` command with exit0, then finished after three turns/23.5s. There
were no parser failures. Different tasks are involved, so this is fresh delivery
evidence, not a controlled speedup against the prior lap.

The raw candidate failed boolean context rejection, preservation of extra input
fields and the required serialization-error return. Codex corrected these,
restored literal nonempty-string handling, and integrated the learner. Hermes
independently identified the three main defects in35.142s. Its tuple example is
incorrect (`json.dumps` serializes tuples), and its comments about the repository
file do not negate the captured worker file or successful worker syntax check.
The raw review remains advisory; no execution results were supplied to it.

[Raw candidate, decision, review and trace](evidence/20260921-workload-candidate.json).
[Independent correction, identical-input replay and report evidence](evidence/20260921-workload-comparison.json).

## Window and accounting

Lap11:30–11:55 UTC; first-file target11:37, generation ceiling11:49, final six
minutes reserved for delivery. Source discovery and a local `USD0.10` typo cost
the dispatch target; the contract-correct `USD 0.10` submission succeeded with no
API call for the failed construction. The first-file target was missed and Derek
was asked about finishing the already-queued single lap. No separate retry was
started. The worker file was observed11:38:52; the corrected report was saved
11:45:00. Build/review generation ended before the ceiling; AM4 was released.

One new JEV request:947 input tokens, estimated USD0.000039774. Cumulative15
attempts: USD0.000498582 known-usage estimates plus USD0.005505024 uncertain
reservations, USD0.006003606 booked against the unchanged USD0.10 cap. Codex
effort and local hardware/power are not part of that API accounting.
