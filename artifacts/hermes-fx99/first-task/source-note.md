Fleet-capacity correction — AM4 (2026-09-19)

Tool-execution note: read_file on inventory.toml succeeded (truncated at 22,000 bytes). read_file on knowledge/am4_gpu_catalog.json was blocked by a Hearth guard ("knowledge/*.json is written only through the knowledge tool family"), so the catalog's exact fields could not be inspected. query_knowledge (host=am4, topic=capacity, limit=2) succeeded.

Stale fields (source facts, inventory.toml, [[node]] name="am4")
- gpus: lists exactly ONE card — nvidia-rtx-5070, uuid GPU-a1f65cc0-44d9-7854-6785-7d93e686da2f, bdf 0000:09:00.0, vram_gb 11.94. No RTX4070Ti entry (GPU-dafbdbfc-23af-0c97-112d-dc17695c2aa8).
- purpose: still describes "RTX 5070 driver 595.84", the oxen facade :8090, and Ollama/qwen2.5:14b as the inference story — superseded by the native Dense27B server and the stopped old service/Ollama.
- No :18090 anywhere in the file; the facade alias am4-dense-27b is not modelled.

Fresh observations (operator, 2026-09-19 — NOT from the sources above)
- TWO physical cards: RTX5070 (a1f65cc0) + RTX4070Ti (dafbdbfc).
- Native Dense27B server READY at :18090: n_ctx=131072, total_slots=1, CUDA q4_0 KV.
- Old managed :18084 service and Ollama STOPPED; alias am4-dense-27b now maps to :18090.
- Primary Hermes, compression, and builders on this alias share ONE physical slot.
- OMEN keeps eight production slots unchanged. FX99 controller uses CPU, not a new GPU model.
- /api/ps read: coder7B resident on FX99. Earlier Mistral residency was stale — do not assert current residency from docs.

Unmeasured performance (do not treat as current)
- findings.json (watermark 2026-09-19): am4-dense + Qwen3.8-27B-Q4_K_M = 22.37 tok/s, 1/1 sample, confidence 0.33, dated 2026-09-18; am4-moe + gpt-oss-120b = 6.7 tok/s, 1/1, dated 2026-07-30. Both marked "historical; verify against a live resource snapshot." Not a capacity measure.

Smallest code/catalog correction
1. am4.gpus: add the 4070 Ti entry {type="nvidia-rtx-4070-ti", uuid="GPU-dafbdbfc-23af-0c97-112d-dc17695c2aa8", vram_gb=<measured>}; keep the 5070 entry.
2. checks: replace/annotate the oxen-facade :8090 note and add {service="dense27b", port=18090}; note :18084 + Ollama stopped.
3. purpose: state two cards, one shared Dense27B slot at :18090 (alias am4-dense-27b), :18084/Ollama down.
4. Catalog (am4_gpu_catalog.json): must be updated via the knowledge tool family, not read_file — mirror the two-card gpus array and the single-slot :18090 endpoint so schedulers stop assuming one 11.94 GiB card.

Three regression tests
1. Parser test: tomllib-parse inventory.toml; assert the am4 node has exactly 2 gpus with the two exact UUIDs and that gpus length matches the catalog's am4 gpus array length (guards against single-card regressions).
2. Alias/endpoint test: assert the am4-dense-27b facade alias resolves to 192.168.12.233:18090, and that no active check/service references :18084 or am4 Ollama :11434 as a serving endpoint (stopped services must not be probed as capacity).
3. Slot-consistency test: assert total_slots=1 for the am4-dense27b server and that every consumer registered on the am4-dense-27b alias (primary Hermes, compression, builders) is counted against that single slot — i.e., concurrent consumers on the alias never exceed 1 active slot (no phantom slot double-counting).

Out of scope / not done: no dispatch, shell, writes, or model rotations; catalog contents unverified (guard-blocked); no current-residency claims for FX99 beyond the /api/ps coder7B read.