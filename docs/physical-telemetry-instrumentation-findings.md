# Physical Telemetry Instrumentation — Findings

Date: 2026-07-02
Role: Laboratory Instrumentation Architect
Scope: the organism's physical layer — thermal, power, fan, GPU/model residency, VRAM, load/unload, sustained throughput decay
Inputs: [CAPABILITY-ROADMAP.html](../CAPABILITY-ROADMAP.html) (S1–S8 shipped, 110/110 tests), [TWO-ECONOMIES-WIND-TUNNEL.html](../TWO-ECONOMIES-WIND-TUNNEL.html) (Δ2 proposal), `contracts/capacity-observation.v1.schema.json`, `fixtures/workflow/runs/*/artifacts/observations/*.json`, [adaptive-telemetry-driven-orchestration.md](adaptive-telemetry-driven-orchestration.md)
Status: analysis complete; one draft-ready, non-breaking schema change landed (see "Status" below). No projector, fixture, or fleet wiring added — nothing released.

## 1. Proposed observation model

Physical telemetry belongs in `capacity-observation.v1` as a nested, nullable-only summary block — not a new contract, not a raw time series, and not flattened into the existing `observed` fields.

- Home: `observed.physical` (parallel to `observed.{runtime_s, ttft_s, tokens_per_s, ...}`), so "no physical telemetry for this run" is one null check, not six independent nulls.
- Shape: per-run **summary** values (peak / sustained-average), matching how `observed` already treats `ram_gb_peak` / `vram_gb_peak` — never a per-second series. Raw series belong in OTel/Prometheus per the existing adaptive-telemetry design; only the scheduling-relevant summary crosses into durable evidence.
- Additive discipline: every new field nullable, `additionalProperties: false` preserved, no existing `required` touched — so the six existing observation fixtures (`obs_201`..`obs_401` etc.) stay valid unmodified.
- New top-level field: `hardware_profile_id` (nullable) — identity of the GPU/driver/thermal-solution lineage the observation was taken under. This is the one addition not implied by the wind-tunnel doc's Δ2 sketch; see §5.

## 2. Metrics that must never become authored state

Everything in `observed.physical` is a sensor reading (an authored "observe-this" declaration, Derek's irreducible role per Constitution Residue) — never a belief. The line sits one layer up, at anything computed *from* these readings:

- Thermal envelope, sustained-decay coefficients, warm/cold bias — must be **findings** (`finding.v1`), never hand-set anywhere.
- Qualification-status effects from thermal behavior — must flow only through the existing S6 decay mechanism; no special-cased "this node runs hot, mark unqualified" edit.
- Capability envelope thermal dimensions (named in Δ2) — computed by the association engine into `capabilities.json`, same as every other field there. No write path may set them directly (mirrors the S5b rot test already in the suite).
- The **operating budget** (how hot/loud/worn the lab may run unattended) is explicitly *not* a metric — it is an authored risk-acceptance object, the same species as an experiment plan's `risk_accepted`. It must live in its own authored contract, never inside `capacity-observation.v1`, or it would look like derived truth while actually being will.

## 3. Finding candidates that should emerge (not be pre-built)

Ordered by expected evidence density, each requiring the same evidence-log discipline already enforced for `build|ollama` (S5) and the vLLM MoE crash (held at finding-level on 3 samples / one workflow):

1. **Sustained-load decay** — named in the wind-tunnel doc itself. "Combo X's `tokens_per_s` drops N% after M minutes above T°C." Requires ≥2 workflows before generalizing past finding → association.
2. **Cold-load latency tax** — `model_residency=cold_load` correlates with elevated `ttft_s` / lower early-window `tokens_per_s` versus `warm_resident` on the same builder+model.
3. **Power-throughput ceiling** — `power_w_peak` plateaus while `tokens_per_s` does not scale further. Directly useful to the Δ3 knowledge-per-hour objective: a node pegged at its power ceiling with no further throughput gain is a candidate for the idle-drain queue (Δ4) rather than continued dispatch.
4. **Thermal-induced failure correlation** — `outcome: oom_crash | error | timeout` clustering with elevated `gpu_temp_c_sustained_avg` or throttled `clock_mhz_avg`. This is the existing `failure_class → cause` association type (S5), not a new finding type.
5. **Fan/duty as an early-warning proxy for #1** — only worth stating as its own finding if it predicts decay *earlier* than temperature does; otherwise it stays a supporting field inside #1's evidence rather than an independent finding.

None of these should be scaffolded ahead of evidence.

## 4. Telemetry priorities

| Field | Priority | Why |
|---|---|---|
| `gpu_temp_c_sustained_avg` | High | Direct input to sustained-load-decay; more predictive than peak temp |
| `model_residency` | High | Cheap to capture, directly gates cold-load-tax finding, already a discrete state not a continuous sensor |
| `power_w_avg` / `power_w_peak` | Medium-High | Enables power-throughput-ceiling finding; feeds Δ3's knowledge-per-hour objective |
| `clock_mhz_avg` | Medium | Throttle signal; supports thermal-failure correlation |
| `gpu_temp_c_peak` | Medium | Secondary to sustained-avg; useful as an outlier flag |
| `fan_rpm_avg` | Low-Medium | Monitoring-only in isolation per the adaptive-telemetry doc's own caveat ("unless a specific decision uses them") — promoted here only as a supporting feature for #5, not a standalone priority |
| Raw per-second series (any field) | Excluded | Belongs in OTel/Prometheus per existing design; never durable evidence |

## 5. Instrumentation risks

- **Hardware evolution vs. historical comparison (the sharpest gap, previously unaddressed).** A thermal-envelope or throughput finding computed today on omen-worker-1 is implicitly conditioned on that exact GPU, driver, and thermal solution. A GPU swap, driver update, or even a repaste silently invalidates prior findings — nothing in the original Δ2 sketch forces re-derivation. Mitigation landed: `hardware_profile_id` on `capacity-observation.v1` (nullable, additive), so associations can be scoped to a profile lineage and S8 coverage can eventually report "this capability's thermal envelope predates the last hardware change" as its own gap type, alongside `stale_capability`. This is presently just an identity field — no reducer consumes it yet.
- **Sensor reliability / source of truth.** The adaptive-telemetry doc flags (open question #4) that the final Intel B70 telemetry collector path is unvalidated. Populating `observed.physical` from an unreliable source produces confidently-wrong evidence, which is strictly worse than an honest null — coverage reporting the gap is safer than a bad reading treated as ground truth.
- **Cardinality creep.** Nothing here should become a metric label; these are per-run summary fields inside an evidence artifact, consistent with the existing design's cardinality guidance (`workflow_id`/`run_id`/artifact paths stay out of metric labels).
- **False confidence from sparse thermal data.** Same mitigation already in place for every other finding type — evidence grade, sample count, no generalization from one workflow. No new machinery needed, just applying the existing discipline to a new evidence field.

## 6. Constitutional concerns

- **Clean:** raw `observed.physical.*` fields are events/observations, correctly authored-by-instrumentation, not by belief.
- **Clean:** sustained-decay, cold-load-tax, and thermal-failure findings are all correctly projected, never hand-set, and correctly require repeated-evidence gating.
- **Risk, now mitigated at the identity level only:** hardware-lineage tracking was absent from the original Δ2 sketch. Without `hardware_profile_id`, a hardware swap would have caused *silent* projection drift — a derived truth that still looks "derived" but is derived from a stale premise nobody re-checks. That is authorship-by-omission of the staleness itself, a Constitution Clause-1 violation by a side door the constitution didn't anticipate. The field now exists; the re-derivation trigger (a reducer that treats hardware-profile change as an evidence-time discontinuity, the way S6 treats evidence-watermark staleness) does not exist yet and is the natural next increment.
- **Correctly scoped:** the operating budget stays an authored risk-acceptance object, not a metric — matches Clause 2 exactly as the wind-tunnel doc states.
- **Correctly deferred:** no thermal envelope or decay coefficient should be written into `capabilities.json` ahead of the association engine computing it — same restraint as S5b's rot test, extended to the new field set.

## Status

Landed (draft, not released, per fleet-pacing):

- `contracts/capacity-observation.v1.schema.json` — added `hardware_profile_id` (top-level, nullable) and `observed.physical` (nested, all fields nullable: `gpu_temp_c_peak`, `gpu_temp_c_sustained_avg`, `power_w_avg`, `power_w_peak`, `fan_rpm_avg`, `clock_mhz_avg`, `model_residency`).
- Verified backward-compatible: 110/110 existing tests pass unchanged (`python -m unittest discover -s tests/workflow`), no fixture modified.

Not done, intentionally:

- No projector (`project_associations.py`, `project_findings.py`, `project_coverage.py`) reads these fields yet.
- No fixture populates `observed.physical` or `hardware_profile_id`.
- No reducer treats a `hardware_profile_id` change as an evidence-time discontinuity.
- No fleet node collects or reports this telemetry.
- No operating-budget contract exists yet (the one authored object this delta requires).

### Update 2026-07-03 — model_residency reclassified as derived (Δ2 verdict, stream C1)

Per the constitutional review's Δ2 verdict, `model_residency` (the §4 priorities-table "High" row, and the last field in the Status list above) was an authored warm/cold classification hiding inside telemetry. The contract now separates the two: `observed.physical` carries four **raw sensor facts** a collector can report without any threshold — `model_loaded_at_start`, `model_load_count`, `model_unload_count`, `model_load_s` (all nullable, additive) — and `model_residency` gains a `description` marking it **DERIVED** (computed by projection from those raw fields; producers/collectors must never set it directly). The enum is unchanged; removing it would be breaking. The four raw `model_*` fields are what collectors report; the residency classification is projected downstream. That projection does not exist yet (no evidence exists — the constitution forbids scaffolding beliefs ahead of evidence), and the collector is stream C2.
