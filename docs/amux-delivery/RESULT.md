# Amux local delivery — result, 2026-10-13

The native Amux worker submitted real HEARTH work that executed on AM4. The reusable offline preparer and citation sidecar are implemented. The explicit AM4 thinking adapter is built and reviewed but not deployed. The three-backend pool remains staged; no new production verdict or automatic candidate application occurred.

## What was built

- `tools/ops/amux_delivery_prepare.py`: validated brief.v2 arguments, pinned sources, bounded reports and idempotency; normal callers leave backend/model/procedure selection to HEARTH.
- `tools/ops/amux_delivery_sidecar.py`: an offline derivative that repairs a repeated short Rust quotation only when the named function identifies a unique occurrence. It preserves prose and original artifacts, counts repairs, and leaves semantic review required.
- `hearth/toolsurface/inference.py`: for explicit thinking on `am4-vllm` / `am4-dense-27b`, require trusted `reasoning_effort="xhigh"` and send it on the wire. Missing/invalid configuration refuses before network dispatch. Ordinary/off/unrelated calls retain their behavior.
- A Codex `rnd` skill, adapted from Derek's existing Claude skill and memory. The tracked mirror is `../agents/codex-skills/rnd/SKILL.md`; Claude's per-prompt hook was not ported.

## Real observations

Eight one-call candidates were produced through native Amux: two each on OMEN-A, OMEN-B and AM4, then two AM4 enabling attempts. All eight finished with `stop` and remain at awaiting_review. The detailed original artifact identities and separate form/substance findings are preserved in `evidence/qualification-review.json`; the continuation addendum supersedes an earlier retry-limit stopping interpretation.

| Observation | Result | Interpretation |
| --- | --- | --- |
| AM4 initial log review `work_e93babeb019650e22a9ecf8e7b995002` | 16.696s, 2630 input / 641 output tokens, 226 words | Real AM4 execution; several substantive claims still wrong |
| AM4 initial listener review `work_eb2686322ad4c268e43bddb78a783c49` | 12.941s, 2405 input / 577 output tokens, 227 words | One ambiguous quote; substantive mistakes remain |
| Sidecar applied to that listener result | Unsupported quotes 1→0; 227→227 words; one repair | A real form gain; prose and wrong claims unchanged |
| AM4 source-checklist log review `work_a04362c9e3df133a37df12842b8321f2` | 15.182s, 3358 input / 657 output tokens, 192 words | Two criteria improved; proposed test still wrong |
| AM4 enabled listener review `work_dc8cb6b22991e89a02b11b8d6d8290c7` | 15.225s, 666 output tokens, 246 words against max240 | Quotes resolve; overflow and substantive errors remain; no-op sidecar preserves overflow6 |
| OMEN carry `work_6c5ef756bc7339c3afe95d7fe4f77eb1` / `job_2c18cd049d87e4da2c27bc4509538885` | 461304ms, 3271 input / 24576 output tokens, all24576 reported reasoning, finish `length`, empty visible draft | Work-stage budget exhausted; no candidate or substance verdict; raw reasoning was not read |

OMEN-A/B temperature-0 paired outputs were byte-identical. Root observed both AM4 GPUs at98% utilization during the first AM4 proof; this is an observation in the receipt, not a separately archived telemetry trace.

## AM4 enabling edge

The live AM4 engine reports49152 context; the existing facade admits24576 output tokens. HEARTH still declares16384 context, ordinary4096 output, and no deliberate allowance. Its fleet facade default sets effort to `none` when absent, overriding the old adapter's explicit thinking=true. The scoped adapter fixes that request ambiguity; enabling the larger declared profile remains a fresh H-3 decision.

The proposed declaration preserves ordinary4096 output and one slot, adds deliberate24576 and effortxhigh, and aligns only the three-dense AM4 context row to49152. No GPU, engine or facade restart was made or is justified by the observed state. Code deployment requires a controlled door reload. Declaration patches and the approval proposal are preserved under `operational-drafts/`; the proposal is not an approval.

## Existing validation, not rerun at landing

- Preparer:16 focused tests and71 existing checks passed.
- Sidecar:25 focused tests,146 existing checks and contract.selfcheck passed; independent review corrected five edge cases.
- Thinking protocol: five new checks covered on/off/unspecified/scoped/refusal and the fake-engine facade path. The final existing-suite invocation ran196 tests plus22 subtests in14.23s. The initial59-test run had one fixture failure caused by unpinned escalation trying gcloud; fixture isolation was fixed, not production escalation.
- All this evidence predates landing. The broader validation workflow exceeded what the R&D slice needed; see `RETROSPECTIVE.md`.

Logs remain under `/home/derek/work/amux/scratch/am4-delivery-20261013/`. Selected summaries are archived in `evidence/`. Build receipt: `br-20261013-190915-a5ea463f`. Originals under `runs/operator/` remain immutable.

## Runtime left in place

The ordinary deep route is still singleton OMEN-A. Route SHA256 `53cbf3203dbd4fb8d48adeb9065cf6e564dfe37da8197a0a3e722d03d7eac1e0`; procedure-table SHA256 `2ed235f84c7e1596b8514f44e7e82870717c6d81c4a823a4fbf954522e8c185b`. The drain timer was restored active/waiting and the last preflight observed zero work, leases, tenancy and local execution jobs. The56 historical external BF6 queued records were left untouched.
