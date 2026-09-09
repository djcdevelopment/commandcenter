# Workload audit — what the lab was actually asked to do

**Lap 0 of the multi-tenant throughput program.** Read-only reduction of
`hearth/var/ledger/events.ndjson` (243,411 events, 2026-07-04 → 2026-09-09) by
`hearth/analysis/workload.py`. Machine-readable output: `knowledge/workload.json`.

> **Read this as a capability audit, not a demand curve.** The traffic below is shaped by what the
> lab can currently accept, not by what anyone wants from it. Derek's framing, and it is the correct
> one: *"a reason we'd be designing for a workflow that doesn't exist is because the system can't
> support it yet so i didn't invest in shaping it."* Nothing here should be read as demand, and
> optimizing for this shape would be circular.

## Channel blind spots — stated first

- This ledger records **door-mediated** calls only. Production llama-server serves HTTP directly on
  `:8082` under ArcServe; that traffic never reaches here. **This file cannot say how busy the cards were.**
- The span crosses fleet changes — `am4-oxen` died 2026-08-20 — so backend shares are historical, not
  a current configuration.
- Only `local_generate` carries token counts. Every other tool contributes events but no depth.
- 838 inference calls is a small sample for the deep tail (18 calls at ≥32K).

## The lab is single-tenant, and the ledger is mostly polling

| | |
|---|---|
| events | 243,411 (0 unparseable) |
| inference calls | **838 — 0.34% of events** |
| wall time recorded | 22.65 h total; **4.33 h of it inference** |
| peak concurrent inference | **4** |
| inference duty cycle | **0.34%** |
| caller concentration | `claude-frontier` **756 / 838 = 90%** |

84% of all events are status polling (`kernel_status` 53k, `get_image_status` 44k,
`list_execution_providers`/`list_operations`/`list_owned_executions` ~31k each). By profile,
`irc-adapter` is 51% and `imagegen-admin` 20%.

**Our benchmarks assume sixteen concurrent clients. The door has never seen more than four.** That
assumption is an aspiration, not a measurement — which is precisely the point above: there is no
multi-tenant workload because there is not yet a multi-tenant system.

## Depth is bimodal, and the deep tail is real

| | tokens_in |
|---|---|
| median | **192** |
| p90 | 10,858 |
| p95 | 17,404 |
| max | 205,022 |

| threshold | calls | share |
|---|---|---|
| ≥2K | 164 | 19.6% |
| **≥8K** | **107** | **12.8%** |
| **≥32K** | **18** | **2.1%** |

Half of all calls are under 200 tokens; an eighth are past 8K. ADR-0039 puts the 27B at **2.63×** the
incumbent's jobs/hour at 8K and **5.49×** at 32K.

## The finding: the deepest work is the work we send away

Routing of the 107 calls at ≥8K:

| routed_by | calls | backend |
|---|---|---|
| `pinned:gcp-gemini-pro` | 61 | cloud (trial credits) |
| `tag:research` | 19 | am4-moe |
| `pinned:gcp-gemini` | 18 | cloud |
| `quality-good:tag:cloud-overflow` | 6 | cloud |
| `payload:cloud-overflow:gcp-gemini` | 2 | cloud |
| `pinned:omen-arc` | 1 | local |

At **≥32K: 18 calls — 15 `gcp-gemini-pro`, 3 `gcp-gemini`, zero local.**

**`omen-arc-27b` — the pin-only depth specialist ADR-0039 created for exactly this regime — appears
nowhere in 243,411 events.** It has never served a call.

Across all 838 inference calls, **89.0% carry a caller pin** (`pinned:*`). Family routing has fired
**4 times**, all on 2026-09-08, all from `codex-cli`, all resolving to `tag:default` because their
depths (3639 / 1229 / 194 / unknown) sit below the 8192-token override threshold. So:

**The ADR-0039 depth override has never fired in recorded history** — not because the family lane is
broken, but because the calls that reach it are shallow and the calls that are deep are pinned.

### Why this is rational, not sloppy

`_family_route` returns `plain` on any caller pin, so a pinned call cannot take a depth override. But
the pinning is *following our own guidance*: `CLAUDE.md` says of large reads, "let the router pick, or
**pin a gemini rung**", and describes `gcp-gemini-pro` as "the premium reach: 1M-token context …
for the hard, large-context sub-tasks flash can't carry."

And the guidance is itself rational, because **`omen-arc-27b` is pin-only and costs a model swap**
(20–57 s load; ADR-0045). Nobody pays a minute of swap for one 32K call. The depth specialist is
unreachable in practice not because routing failed but because **the swap cost exceeds the value of
any single deep call.**

That is the causal chain worth keeping:

> deep work exists → the local specialist that wins it costs a swap → no single call justifies the
> swap → callers pin cloud → the local rung never runs → its advantage stays theoretical.

The lever is not the router. **It is the cost of changing what is resident** — which is exactly what
the rotation lane's KV save/restore (~3 s vs ~100 s re-prefill) attacks, and exactly what a
group-formation cost model would price: batch the deep calls, pay the swap once.

## Reconciliation

`knowledge/offload.json` reports `tokens_in` **3,168,190**; the ledger's 838 token-bearing rows sum to
**3,168,190** — exact. The projection counts **1,307 calls** against the ledger's 838 token-bearing
rows, so ~469 dispatches reach the projection without a recorded cost. That gap is a finding, not a
rounding error, and it bounds how much of this audit's depth analysis is blind.

## What this gates

- **FF7 (SYCL/F16) stays deferred.** The mix is not prefill-bound in any way this ledger can show —
  it is a bursty, shallow-median, single-tenant trickle at a 0.34% duty cycle. A backend constant
  factor is not the binding constraint.
- **Lap 1 and Lap 2 proceed**, but their justification changes: not "optimize the observed workload"
  — there isn't one — but "build the capability whose absence is why there is no workload."
- **The swap-cost lever moves up.** Anything that lowers the price of changing residency (KV
  save/restore, weight staging, grouping deep work) directly attacks the chain above.

## Reproducing

```bash
python -m hearth.analysis.workload --out knowledge/workload.json
```

Deterministic: the output embeds no wall clock, so an unchanged ledger reduces to identical bytes and
a diff means the corpus moved.
