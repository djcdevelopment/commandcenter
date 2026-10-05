# AM4 FP8: explicitly select FlashInfer native decode

The next evidenced candidate is **FlashInfer with TRTLLM/XQA disabled explicitly**, retaining FP8 KV only in a second paired arm. The installed vLLM code supports this configuration directly. It avoids the specific XQA architecture restriction without changing installed code or bypassing a check. No candidate was applied or run by this builder.

## Failure boundary

`/home/derek/work/lab-rnd/research/evidence/bench27-am4-recipe-20261005/F134link-prepared/start.log` SHA256 `0c5ac1f09d20665a1a0129c6d162037b5319311bcc1c74e9f05a70362be504ba` shows FLASHINFER selected at line 70, rank 0 metadata using `decode_backend=xqa`, fp16 Q, fp8_e4m3 KV and `arch=sm120` at line 97, then TP1 raising the XQA architecture error at lines 250–264. The call chain is vLLM FlashInfer forward:2472 → FlashInfer decode:3797 → xqa.py:403. That installed function explicitly rejects CUDA major capabilities outside `[9,10,12]`. SM89 is excluded.

This is a restriction of the chosen **XQA implementation**, not proof that FP8 KV cannot operate on the mixed pair. The log does not itself establish why rank 1 inherited/selected an incompatible path; do not present an unverified capability-cache explanation as root cause. The parent separately repaired toolkit-local linker lookup with lib64/libcudart symlinks and restored the E134 auto-KV control. That history stays separate from this backend-selection arm.

## Narrow supported switch

Installed vLLM 0.30.0 `config/attention.py:69` declares `use_trtllm_attention: bool | None`; false means do not use TRTLLM inside FlashInfer. `utils/flashinfer.py:635` immediately returns false from `can_use_trtllm_attention` when that setting is false. In the FlashInfer metadata builder, lines 810–827 then set the TRTLLM decode choice to false and its kernel to None. The log at lines 902–917 will report `decode_backend=flashinfer-native`.

Exact reviewed candidate CLI addition:

```bash
--attention-backend FLASHINFER \
--attention-config '{"use_trtllm_attention":false}'
```

`--attention-backend` is registered at installed `engine/arg_utils.py:1008`; `--attention-config` at 1726. Use the JSON boolean false, not the string "false". If a future frozen control already has an attention-config object, merge this field without dropping its other settings; do not pass conflicting duplicate flags. No environment toggle is required. The explicit FLASHINFER selection ensures this arm cannot silently choose a different full attention backend.

Native decode uses `BatchDecodeWithPagedKVCacheWrapper(use_tensor_cores=True, backend="auto")` for this non-NVFP4 cache (`flashinfer.py:1214–1228`). In installed FlashInfer `decode.py:1675–1704`, FP8 native tensor-core decode chooses via `determine_attention_backend` and invokes `get_batch_prefill_module`. `utils.py:587–600` selects FA3 only for SM90a with the supported combination, otherwise FA2. For SM89 and SM120 this is the native FA2 implementation, not the XQA auto selector. vLLM `get_q_data_type` lines 959–970 explicitly keeps model-dtype Q for SM89/SM120 with FP8 KV, because their FA2 path cannot consume FP8 Q. Hence the source supports fp16 Q + fp8 KV for both members; the actual pair still needs a successful load and measurements.

Disabling TRTLLM applies to prefill and decode wherever those routes were otherwise enabled. It can change throughput, workspace, graph capture and memory behavior. It is an explicit backend arm; record it in recipe evidence and final serving_profile_version. Keep the repaired toolkit and all unrelated inference flags fixed. No header edits, architecture spoofing, environment check bypass, silent runtime substitution or package mutation is proposed.

## Drafts and qualification sequence

`bench27-fp8-native-candidate/` contains a read-only captured control, exact patches, two scripts, hashes and installed-source excerpts with source file hashes. Captured control SHA is `1343633c1109f2abb32398c577f356ad6a5b72b775989aa4ec8520b5485b6316` (the restored E134 script observed at read time). It retains CUDA13.4 toolkit and isolated FlashInfer workspace settings. Verify this against the owner's selected control before application.

1. Independent Opus review of these drafts and source evidence.
2. `native-auto.sh`: same auto-KV control plus explicit FLASHINFER/native decode. SHA `bfe8b1af2e68f0bfc40b2cd2b6cafa1e253ed66b8b5ebe953816ec949a98f1b5`. Under the established zero-lease/guard/backup/restore protocol, load and run the same fixed small thinking and exact-token needle probes. This is the new backend baseline.
3. Only after baseline acceptance, `native-fp8.sh`: identical native backend plus `--kv-cache-dtype fp8`. SHA `d0ac2f9bb998ed5ae3750356b34937f462d9700ff247dfcd2931d6b153b1f970`. Use a new evidence directory and identical probes/window/workload. Compare against native-auto, not only the older auto-selected attention baseline.
4. Require readiness, actual FP8 KV in logs, explicit native decode resolution, full successful probe results and cancellation/guard invariants before a verdict. Preserve failed startup evidence and restore the accepted control on failure. Repeated worker logs may be suppressed by info_once, so do not assert two-rank confirmation from a rank-0 log alone; record that visibility limit alongside TP readiness.

The builder performed only source/log reads and local draft generation. Shell syntax and argv deltas are checked offline; no vLLM parser import, model load, GPU kernel, service operation, package install or remote file write occurred for this diagnosis. Source evidence is stronger than guessing a version-dependent environment variable but remains **source-supported, not runtime-qualified**.

## Broader alternative: TRITON_ATTN

An explicit alternative is `--attention-backend TRITON_ATTN`, with `--kv-cache-dtype fp8` only in its second paired arm. Installed `triton_attn.py:289–304` declares fp16 inputs and `fp8`, `fp8_e4m3`, `fp8_e5m2` KV supported; lines 350–352 accept head dimensions >=32 (this model's attention head size is 256), and lines 376–378 do not restrict compute capability. Its metadata declares graph support. The F134link log lists TRITON_ATTN as another potential backend at line 70. This is credible source-level support for the pair, not a guaranteed successful configuration.

TRITON_ATTN replaces the full dense attention implementation and may change prefill as well as decode behavior. Keep existing `--gdn-prefill-backend triton`: that controls the hybrid model's GDN path and is not the same switch. Do not combine TRITON_ATTN with the FlashInfer-specific disabling flag or try both changes in one arm. Prefer the narrower native FlashInfer arm first because it preserves the attention family and targets the exact failure. If it fails, preserve the result and review a separate Triton auto-KV/FP8 pair. No alternate Triton executable draft is included to avoid an unreviewed multi-choice live change.
