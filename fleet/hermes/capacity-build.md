Deliver one real LOCAL delegated build through HEARTH, then return its receipt ID and plan ID in a short complete answer. Do not wait for completion. Do not run probes, model changes, or unrelated tools. This is a reviewed, authorized implementation task; server-side policy enforces manual promotion and the AM4-only pair.

Call create_build_request with:
- repo: C:/work/commandcenter-hermes-fx99
- title: Hermes AM4 native capacity-map correction
- backend: am4-dense
- task: coding
- execute: false
- deliverables: ["hermes-capacity.patch", "CAPACITY-REVIEW.md"]
- acceptance_criteria: ["A patch corrects AM4 two-card inventory/catalog and native Dense readiness/context without counting stopped routes or alias slots twice.", "New CPU tests exercise ready, stopped, stale, wrong-model and shared-physical-slot cases; report exact test output and any unverified GPU placement."]
- request: Use the build brief below verbatim (no Windows paths in this request body).

Then call execute_build_request with the returned receipt_id, mode=delegate, backend=am4-dense, builders=["cc-builder-2","cc-builder-3"], max_age_s=900, task_class=build, promotion_policy=manual, runner_preset=am4-shared-27b. Do this exactly once. If refused, return the exact blocker without changing topology.

BUILD BRIEF:
Produce a small useful capacity-map correction. Authoritative READ-ONLY source is /home/claude/hermes-capacity-source-20260919, recorded baseline b2b7341; do not use the older ~/commandcenter-src reference. Work in your assigned candidate workspace. Do not touch live services, GPUs, credentials, default runner configs, main, or external remotes. Keep the change narrow and deliver within the worker's 410-second budget. Save a partial honest artifact early if needed.

Read the specific baseline files fleet/inventory.toml, knowledge/am4_gpu_catalog.json, hearth/toolsurface/scheduler.py and the relevant existing CPU tests. The catalog is authored source, not a generated projection. Produce a unified git-style patch named hermes-capacity.patch, relative to this baseline, containing the changed source and new tests; write CAPACITY-REVIEW.md with exact paths, assumptions, test command/results, and unverified work. Include the patch itself even if tests cannot run here. You can copy needed baseline files into a temporary directory and use diff generation rather than retyping long files.

Observed AM4 facts, 2026-09-19:
- RTX5070: UUID GPU-a1f65cc0-44d9-7854-6785-7d93e686da2f, BDF0000:09:00.0, 12227MiB (11.94GiB).
- RTX4070Ti: UUID GPU-dafbdbfc-23af-0c97-112d-dc17695c2aa8, BDF0000:0a:00.0, 12282MiB (11.99GiB).
- Native llama-server Dense27B at localhost:18090; clients MUST use authenticated facade http://192.168.12.233:8090, alias am4-dense-27b, canonical HEARTH backend am4-dense. Model models/Qwen3.8-27B-Q4_K_M.gguf; n_ctx=131072; parallel_slots=1. Both builders and Hermes share this ONE physical slot, not three slots.
- Passive authenticated GET /oxen/ready?alias=am4-dense-27b returns {all_ready:true,aliases:[{alias:"am4-dense-27b",ready:true,context_length:131072,parallel_slots:1,physical_resource:"am4:127.0.0.1:18090",model:"models/Qwen3.8-27B-Q4_K_M.gguf",status:200}]}.
- AM4 Ollama11434 and old facade targets8080/8081/18085 are stopped. A catalog entry or open facade socket is not current inference readiness. Preserve historical catalog evidence rather than presenting old rates as current. New native-model rate unknown (null), not a made-up number.
- Runtime health/context do not alone prove full GPU placement. Keep that uncertainty explicit; no inferred gpu_placed:true without independent placement evidence. Do not change the global builder runner declarations: per-run AM4 overrides now exist separately.

Implement these facts in the inventory/catalog and add a small, testable native readiness normalization path used by capture_resource_snapshot. It must preserve model/context/physical_resource, reject stale or wrong-model data, mark stopped native routes unavailable, and treat multiple aliases of the same resource as ONE capacity. Do not expand into rotation/model loading or rewrite the scheduler. Existing read-only snapshots must remain read-only. Do not use unauthenticated engine admin routes. Keep output concise; report any missing integration honestly rather than claiming the old 162-test assay proves this change.
