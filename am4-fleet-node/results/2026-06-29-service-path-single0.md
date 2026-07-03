# AM4 Service-Path Benchmark: single0, 128k ctx

Date: 2026-06-29
Host: `am4`
Artifact: `service-path-2026-06-29T115854Z-single0-ctx131072-depth65536.json`

## Goal

Validate the real Hermes service path on AM4:

1. start a resident `llama-server`
2. wait for backend `/health`
3. wait for facade serve-readiness on `:8090`
4. call alias-routed `/v1/models`
5. run non-stream and stream `/v1/chat/completions`

This is the old `vllama` proof shape, not a synthetic `llama-bench` run.

## Configuration

- alias: `vllama-planner`
- placement: `single0`
- device list: `SYCL0`
- split mode: `none`
- context: `131072`
- KV cache types: `q8_0 / q8_0`
- prompt target depth: `65536`
- realized prompt: `57228` prompt tokens
- generation cap: `32` tokens

## Result

The full service path worked end to end.

- backend health ready: `14.0s`
- facade serve-ready: `0.52s` after backend health
- `/v1/models`: `200`, alias advertised as ready
- non-stream completion: `213.3s`
- stream TTFT: `5.43s`
- stream total: `5.43s`

## Backend timings

From the non-stream response:

- prompt eval: `207573.199 ms` for `57225` tokens
- prompt throughput: `275.69 tok/s`
- generation: `5624.402 ms` for `32` tokens
- generation throughput: `5.69 tok/s`

From the follow-on stream call with prompt-cache reuse:

- prompt eval: `180.86 ms` for `1` token
- generation: `5159.99 ms` for `32` tokens
- generation throughput: `6.20 tok/s`

## Notes

- The earlier harness failures were launcher issues, not model-path failure:
  - `set -u` in `start-hermes-backend.sh` broke oneAPI `setvars.sh`
  - sourced `hermes.env` was overriding benchmark placement env
- Both are fixed in the AM4 node package.
- This run confirms the facade contract is viable on Linux AM4 with single-card SYCL at 128k ctx.
- The first-request cost is dominated by prompt ingest. The second streamed request shows the expected prompt-cache win.
