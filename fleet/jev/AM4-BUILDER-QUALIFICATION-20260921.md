# AM4 Dense builder qualification — stopped before inference

The approved single attempt reached AM4's facade, but authentication returned
HTTP 401, `missing or invalid bearer token`. No model completion, candidate file,
behavioral check, or Hermes review occurred. **Dense builder quality remains
unknown.** This is a failed qualification, not a model-quality result or a useful
code delivery. The existing corrected decision command remains untouched.

Derek explicitly approved one qualification using both AM4 GPUs, up to twenty
minutes, manual promotion only, no downloads, no pilot-gateway restart, and no
permanent JEV route change. The earlier cycle20 continuation approval was also
received late; that lap was already complete and was not rerun as a JEV task.

## Attempt and first blocking edge

The intended useful artifact was a raw Dense implementation of the **exact
cycle20 decision-view brief**, independently compared with the preserved OMEN
candidate. Dense was not given Codex's corrected implementation. Brief SHA-256:
`26eeb8b73188090dc3657dd6d486e63f4dee3afe2462af33573ec44634baaea3`.

| Event | UTC, September 21 |
| --- | --- |
| Window began | 22:30:13 |
| First-file target / latest useful dispatch | 22:35 / approximately 22:33 |
| Both GPUs observed idle | 22:30:40; 88 / 15 MiB, no compute process |
| Owned Dense seat started | Before dispatch; native 131072 context, one slot |
| Single builder launched | 22:33:13 |
| HTTP 401 ended attempt | 22:33:14; 0.071 seconds in worker loop |
| Owned model released, owner cleared | By 22:34:11 |
| Generation / delivery ceilings | 22:45 / 22:50 |

The first edge ended the lap. No second request was sent, no credential was
copied or replaced, and no alternate caller or native endpoint was used to get
around the rejection. There is no first-file success to report. An authentication
check before GPU loading would have avoided this short, unnecessary reservation.

## What changed, and what did not

An isolated operator-run qualification adapter was staged on cc-builder-2 under
`/home/claude/fleet-worker-node/qualifications/am4-dense-cycle21-20260921`.
It used the already installed, hash-verified `am4-shared-27b` preset and its
existing builder-specific credential. This was **not** an admitted JEV/conductor
dispatch, and the caller was not relabeled to evade their OMEN-only policies.
The explicit user authorization scoped the standalone AM4 attempt.

The adapter reused the installed `agent_openai` loop and cycle17's qualified JSON
decoder, requested 4096 output tokens and `reasoning_effort: none`, and used a
180-second loop budget with a 200-second external supervisor. A start marker
prevents accidental reuse of the same lap. Its public trace omits private thought
fields and credentials. All setup and adapter code is Codex work.

The intended comparison had declared differences: native chat messages versus
Hearth's serialized conversation transport, explicit Dense reasoning setting,
and an operator-driven dispatch instead of JEV/MechNet scheduling. With zero
successful completions these differences support no speed or quality inference.

Exit hashes match entry hashes for both worker adapters, the shared resolver,
default `runner.json`, and the parked AM4 preset. No shared admission rule was
changed, no service restarted, and no source or candidate was promoted. The
temporary model was unloaded; private evidence remains on the worker. OMEN's
resident model and production gateways were not changed.

## Evidence, cost, and next target

- [Machine-readable attempt and restoration evidence](evidence/20260921-am4-qualification.json)
- [Exact qualification adapter](evidence/20260921-am4-qualification-runner.py)
- [Previous OMEN result and known defects](DECISION-EXPLANATION-20260921.md)
- Parent receipt: `br-20260921-223040-4f487437`.

One builder process, one rejected completion HTTP request, zero successful
completions, zero JEV calls and zero Hermes reviews. Incremental JEV cost is zero;
Codex usage and local hardware/power are not included. This attempt is separate
from the curated twelve-task JEV outcome history, which was not changed.

Next target: reconcile the **intended builder-specific credential** with AM4's
active facade caller registration, then verify a read-only authenticated route
before loading GPUs for another explicitly bounded build. The observed 401 does
not establish whether the worker key, registry, or running facade configuration
is stale. That cause was not investigated in this lap. Do not borrow Hermes's
credential, disable authentication, switch to a native port, or infer admission
from the parked preset's existence. The forbidden pilot-gateway restart remains
out of scope. A new build requires a new attempt/window, not replaying this lap.
