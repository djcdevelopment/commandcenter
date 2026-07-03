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
3. **Power-throughput ceiling** — `power_w_peak` plateaus while `tokens_per_s` does not scale further. Directly useful to the Δ3 knowledge-per-hour objective: a node pegged at its power ceiling with no further throughput gain is a cand

## 4. Status

- `model_residency` is now a projection-derived classification computed from the raw `model_*` fields. Collectors must report the following raw fields: `model_loaded_at_start`, `model_load_count`, `model_unload_count`, `model_load_s`. The field `model_residency` must never be set directly by producers or collectors.

- All six existing observation fixtures (`obs_201`..`obs_401`) remain valid and unmodified.

- The `hardware_profile_id` field has been added as a nullable top-level identifier for hardware lineage.

- Schema changes are additive, nullable-only, and preserve `additionalProperties: false`.

- No projector, fixture, or fleet wiring has been modified — this change is purely contract-level and does not require runtime updates.

- The `capacity-observation.v1.schema.json` has been updated to reflect the new structure and semantics.

- The `docs/physical-telemetry-instrumentation-findings.md` has been updated to document the new raw fields and the derived nature of `model_residency`.

- A new test file `tests/workflow/test_capacity_observation_schema.py` will be created next to validate the schema structure.

---

> **Note**: This change is in full compliance with Clause 1 (no organizational truth authored), Clause 2 (only projections derived), Clause 18 (determinism), and Law 5 (additive, nullable-only). No silent caps — all changes are explicit and documented.

---

### Next Steps

- Implement and run tests for the updated schema.
- Verify that the full test suite passes.
- Generate `BUILD-NOTES-C1.md` with before/after schema comparison.

---

> **Author**: Agent Builder
> **Date**: 2026-07-02
> **Stream**: C1
> **Status**: Ready for testing
