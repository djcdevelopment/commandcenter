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

Restoration attempts both seats even when one fails. Busy seats fail restoration rather than killing surviving requests. An unverified restoration retains the pool fence and exits 2. Existing operator prerequisites still apply: stop dispatch, drain leases, run thermal protection, and hold sole ownership before starting. This harness does not start or disable those services.

A second pass must use a new ID and reverse the treatment assignment. The caller grades the two passes; the harness does not adopt recipes or issue substance verdicts.

Paired preflight binds each backend to its physical router endpoint using `HEARTH_BACKENDS` (default `~/hearth-production/backends-linux.toml`): seat 0 must resolve to `http://127.0.0.1:18095`, seat 1 to `http://127.0.0.1:18096`. Reversed or unknown declarations fail before swapping. Both seats restart before the campaign, including a null-drop-in control, so both begin cold. The baseline seat requires no second restart during restoration.

Restore retries skip seats already verified as restored. Each seat gets up to 120 seconds to drain before a restore that restarts it; the paired coordinator also drains before collecting settled campaign counters. Both members are attempted even if a restore callback raises `BaseException`. A no-op single-seat restore retains its previous behavior; releasing the fence waits for all outstanding requests to drain. Settled campaign counters and traffic during restoration/release are recorded separately. Either contamination voids the run. Unresolved draining/restoration retains the fence.
