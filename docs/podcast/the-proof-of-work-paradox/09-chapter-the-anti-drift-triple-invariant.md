# The HEARTH Wire: The Proof of Work Paradox
## Chapter 09: The Anti-Drift Triple Invariant

**Setting:** A sharp warning buzzer sounds briefly, followed by the tight, relentless rhythm of automated unit tests executing in rapid succession.

---

**ALEX:**
On April 9th, 2026, Derek hit a bug that every distributed systems engineer has experienced in their nightmares. He documented it in [`E:\work\planning\docs\adr\adr-003-anti-drift-contract.md`](file:///E:/work/planning/docs/adr/adr-003-anti-drift-contract.md). Sam, walk us through the crime scene of Bug 1.

**SAM:**
Here is the context from ADR-003:
Farmer had three separate files per run that described what happened:
- `events.jsonl`: The append-only historical log, written after every stage transition.
- `state.json`: The latest atomic snapshot, overwritten after each stage.
- `result.json`: The final terminal outcome written when the pipeline completes or fails.

Now, early in Phase 5 testing, an SSH passphrase blew up during the `Deliver` stage. The pipeline crashed. Derek went to inspect the run folder to see what failed.
He opened `state.json`. It said: `phase: "Delivering"`.
Then he opened `result.json`. It said: `final_phase: "Failed"`.

**ALEX:**
Two files in the same folder, describing the exact same run, giving two contradictory stories.

**SAM:**
And look at what he wrote:
> *"The architectural claim is that filesystem is the source of truth. That claim only holds if the files agree. In early Phase 5 testing, the first real failure surfaced Bug 1... **The filesystem-is-truth claim collapsed the moment we looked at a failure.**"*

**ALEX:**
Most developers would just patch the script, shrug it off as an edge case, and move on. What did Derek do?

**SAM:**
He elevated it to a non-negotiable architectural invariant:
> *"For every completed run — success or failure — `events.jsonl`, `state.json`, and `result.json` must agree on the final phase and stages_completed list. This is an invariant, pinned by regression tests that can never be disabled."*

He didn't just fix the code in `EventingMiddleware.cs` and `RunWorkflow.cs`; he wrote two permanent regression tests:
- `BugRegression_FailedRun_AllThreeFilesAgreeOnFailedPhase`
- `BugRegression_SuccessfulRun_FinalStateJsonAgreesWithResult`

**ALEX:**
Both tests asserted the full tuple: phase, stages completed, and error string. If any future modification ever caused `state.json` and `result.json` to drift by even one millisecond, the build failed immediately.

**SAM:**
And that principle—*Anti-Drift Invariance*—is the exact conceptual foundation of HEARTH’s execution ledger today. In HEARTH's `ADR-0030`, we have the append-only NDJSON execution log, and we have rebuildable SQLite projections. And what does `public_portfolio.py` do? It asserts `projection_replay_verified: true`. The projection must replay every sequence number from 1 to N against the append-only log, and if there is even a single sequence gap or state mismatch, the entire public snapshot is rejected fail-closed.

**ALEX:**
Farmer established that your files must tell the truth, even in catastrophe. But what do you do once a run finishes and the files agree that it succeeded? Who verifies whether the code the agent produced is actually good? In Chapter Ten, we meet the Retrospective Agent: QA as Postmortem.
