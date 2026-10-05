# Paired experiment interface

`python3 -m fleet.experiment_linux run --spec pair.json` accepts the existing single-seat spec unchanged, or a paired spec:

```json
{
  "id": "bench27-mtp3-pass-a",
  "seats": [0, 1],
  "backend": {"0": "omen-dense-27b", "1": "omen-dense-27b-b"},
  "dropin": {"0": null, "1": "zz-mtp3.conf"},
  "campaign": ["/path/to/paired-driver", "--out", "/new/evidence/path"],
  "max_minutes": 10,
  "restore_by": "06:30"
}
```

Add `expect_argv` and `expect_model` maps keyed by `"0"` and `"1"` for the effective argument vectors and served model IDs. Both snapshots precede either swap. The coordinator owns one pool tenancy; independent child state files under the experiment directory persist each seat's snapshot, drop-in application, process argv, and restoration. One campaign process must drive both targets and emit a final JSON report such as `{"calls":{"0":12,"1":12}}`. A single integer cannot attribute a paired run. Either seat's unexplained request count voids the run; opposite count differences cannot cancel.

Restoration attempts both seats even when one fails. An actual restart waits for a busy seat to drain rather than killing surviving requests. An unverified restoration retains the pool fence and exits 2. Existing operator prerequisites still apply: stop dispatch, drain leases, run thermal protection, and hold sole ownership before starting. This harness does not start or disable those services.

A second pass must use a new ID and reverse the treatment assignment. The caller grades the two passes; the harness does not adopt recipes or issue substance verdicts.

Paired preflight binds each backend to its physical router endpoint using `HEARTH_BACKENDS` (default `~/hearth-production/backends-linux.toml`): seat 0 must resolve to `http://127.0.0.1:18095`, seat 1 to `http://127.0.0.1:18096`. Reversed or unknown declarations fail before swapping. Both seats restart before the campaign, including a null-drop-in control, so both begin cold. The baseline seat requires no second restart during restoration.

Restore retries skip seats already verified as restored. Each seat gets up to 3,600 seconds to drain before a restore that restarts it; the paired coordinator samples counters without blocking on control-seat traffic. Both members are attempted even if a restore callback raises `BaseException`. A no-op single-seat restore retains its previous behavior; release samples remaining requests and marks contamination without blocking or restarting restored seats. Settled campaign counters and traffic during restoration/release are recorded separately. Either contamination voids the run. Unresolved draining/restoration retains the fence.

If a bad lever leaves the engine down, restoration removes its drop-in first. Missing metrics permit a recovery restart only when systemd reports a stopped substate (`auto-restart`, `auto-restart-queued`, `dead`, or `failed`), MainPID=0, and the backend has zero active leases. An unreachable active unit is never presumed drained. A failed initial control restart is also recovered; a healthy cold control is not restarted again. Missing counter samples remain unknown and contaminate attribution without preventing a verifiable restore.

Active/activating/deactivating recovery states are polled within the 3,600-second deliberate-request bound. End-of-campaign counter failure is recorded without discarding process exit status, call counts, or renewals. Exit4 is recorded as `model_failed`, distinct from infrastructure failure. Transient launchers must allow this restoration bound rather than imposing a short stop timeout.
