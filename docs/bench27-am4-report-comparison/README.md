# AM4 open admission report comparison (draft)

This is a separate measurement-only driver for the mandatory native auto-KV versus native FP8 report comparison at the same 32,768-token window. The frozen sizing driver is unchanged. Neither this script nor this draft changes a service, installs packages, opens SSH, approves work or deploys a backend declaration. Independent Opus review is required before use.

The driver accepts exactly the authored open admission workload SHA `09e34b4315a076c1ffedd0384d9aea29114071787343a9af0cd30d68cd4ee38e`, source commit `85cf67dafcd433de143c8a65e5576bb13189fd3e`. Its path is `/home/derek/work/worktrees/bench27-routing/docs/bench27-fp8-open-prepared/admission.workload.json`; callers must also pass the expected SHA explicitly. The 24,000-token work and 4,096-token final budgets are unchanged. Each complete rendered turn is counted by the actual tokenizer and must fit the observed window before inference. The reviewed helper overrides both tokenizer and completion model to `qwen3-27b`; original workload bytes and actual wire requests are retained separately.

Preserved controls: fresh two-card guard and sticky trip throughout the response, bounded campaign deadline, SIGTERM/SIGINT owned-socket cancellation, read-only zero-lease checks before both sends, idle engine counters before/between/after, foreign-request counter detection, pinned recipe SHA/argv model path/reasoning parser/window, observed model/root/window, NEW output directory, raw SSE, separate reasoning/content, exact usage consistency, output-budget and stop checks. Neither whitespace-only output nor reasoning tags masquerading as visible output pass. No silent allowance reduction or prompt rewriting is permitted. Model root is additionally checked against `/home/derek/models/qwen3-27b-gptq-int4` in `/v1/models`.

Status `awaiting_review` and exit 0 mean transport/admission checks passed, never substantive acceptance. The parent must grade all three admission criteria against the pinned source, measure form and quote validity separately, count any repairs, and record a frontier verdict for **both** recipes. Workload `brief` contains the substance and form rubric. No sealed source is needed. Exit 4 records inference failure; exit 3 records infrastructure failure. Recipe/workload hash or CLI preflight failures before output creation raise and send no inference.

Before each run, the parent verifies sole ownership, actual live script SHA/environment equals the selected manifest, prior direct cancellation evidence, no engine counters or active leases, healthy model root/window, and a live guard-bound process. Manifest pinning plus `/v1/models` cannot independently prove KV dtype or all live flags; this physical preflight remains required. The driver consumes an already-established localhost tunnel and must run in the parent-owned transient unit bound to the guard. Never run the two arms simultaneously. Preserve native .90, graphs, two engine sequences and all other flags; only KV dtype changes between these two accepted controls.

## Exact arm bindings

- **Nauto90**: manifest `/home/derek/work/lab-rnd/research/evidence/bench27-am4-recipe-20261005/Nauto90-prepared/manifest.json`, SHA `f6264339b38900e1e5e813b4bf6dc2b1d4250ba7c598873478feaf11214c964f`; actual candidate script SHA `3308f2e66029e26182926731bdc42ae0a6bbc73950d45231086cf19c335bb510`.
- **Nfp8-32**: manifest `/home/derek/work/lab-rnd/research/evidence/bench27-am4-recipe-20261005/Nfp8-32-prepared/manifest.json`, SHA `0af355f9a76fbaab19bcc673e76fc1d614d7d99e85a5ced9d512d01dce6b4da3`; actual candidate script SHA `d341e22564dcc86472d8483a3998989c834922e4846ca287a6fdfc03f291c72f`.

For each arm, after its physical preflight, invoke inside the guard-bound unit (replace the uppercase path placeholders with the actual owned paths; OUT must not exist):

```bash
python3 tools/ops/bench27_am4_report.py \
  --helper tools/ops/bench27_am4_probe.py \
  --workload /home/derek/work/worktrees/bench27-routing/docs/bench27-fp8-open-prepared/admission.workload.json \
  --workload-sha256 09e34b4315a076c1ffedd0384d9aea29114071787343a9af0cd30d68cd4ee38e \
  --recipe-manifest /ABS/SELECTED-ARM/manifest.json \
  --recipe-sha256 SELECTED_MANIFEST_SHA_FROM_ABOVE \
  --out /ABS/NEW-ARM-REPORT-OUTPUT \
  --guard-log /ABS/OWNED-GUARD.jsonl \
  --trip /ABS/OWNED-GUARD.jsonl.tripped \
  --coordination-db /ABS/LIVE-COORDINATION.sqlite \
  --port 18094 --timeout 1800
```

No inference was run by the builder. Offline tests use only a temporary localhost fake server, SQLite fixture and guard file. Frozen sizing tests still run against their original contract, including the fixed 24,521 input count and refusal at 32K.

## Draft 49K backend declaration

`am4-vllm-49k.backend.toml.draft` proposes the existing authenticated facade alias, 49,152 context / 172,032 legacy bytes, one door slot, ordinary output 4,096 and deliberate allowance 24,576. Engine sequences remain two in the physical recipe, but one door slot avoids asserting two long conversations. Timeout 1,800 seconds is an operational upper bound, not a measured latency guarantee. The full script hash is embedded in serving_profile_version; it includes the script environment exports. `recipe-bindings.json` binds the canonical manifests, scripts, recipe and environment using hashes freshly computed from those files.

This declaration is **not active**. Parent deploys only after report comparison verdicts and the separate long-sizing comparison support the final choice. The measured work budget here is 24,000, so an advertised 24,576 ceiling is not proof the model generated that many tokens; parent must justify the precise final allowance/admission before deployment. At a frozen 24,521 input, 24,576 reserved output gives 49,097 total, leaving only 55 tokens in 49,152; exact final-turn admission remains separate. No previous recipe's delivery qualification may be reused. Final-profile open-brief qualification, table export and pool activation remain separate gates.

After comparison, parent verifies the final script hash is still the one in the draft, updates the tracked host declaration and live host-config pair under the existing one-writer/zero-lease workflow, checks physical sizing, and qualifies the resulting new serving profile. If the selected recipe changes, regenerate this draft's hash and bind a fresh profile rather than editing flags under the old version.
