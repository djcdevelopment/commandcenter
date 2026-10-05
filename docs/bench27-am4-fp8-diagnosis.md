# AM4 A5 FP8: CUDA compiler/header mismatch

A5 is blocked by a fixable JIT build environment mismatch. It has not established a model quality, FP8 hardware, or capacity limit. No host changes, compilation, model invocation, package installation, or restart were performed for this diagnosis. The campaign owner reports the accepted A4 recipe restored.

## Evidence and exact failure

Evidence log: `/home/derek/work/lab-rnd/research/evidence/bench27-am4-recipe-20261005/A5-prepared/start.log`, SHA256 `833e5d1f45f54dd7e364bcc938ff307b71695362c0dcaea49b8de988df1ac994` at inspection. This log contains earlier service history as well as A5; the relevant first failure is October 5 08:26:55, worker TP1 PID 761978.

At line 180 FlashInfer compiles its FP8 E4M3 KV-cache batch-prefill kernel, Q/O float16, head dimensions 256, with targets `sm_89` and `sm_120f`. The command invokes `/home/derek/.venvs/vllm-cuda-030/lib/python3.12/site-packages/nvidia/cu13/bin/nvcc` and includes the same prefix's `include` directory. At lines 197–198, FlashInfer's bundled `cuda/std/__cccl/cuda_toolkit.h:41` rejects the compiler/header combination. This precedes a successful ready API for A5; downstream engine initialization errors are consequences.

Read-only SSH probes on `10.44.0.2` confirmed:

| Component | Observed value |
|---|---|
| Invoked `nvcc --version` | CUDA 13.4, V13.4.92 |
| `nvidia/cu13/include/cuda_runtime_api.h:139` | `CUDART_VERSION 13000` (13.0) |
| `nvidia/cu13/include/cuda.h:263` | `CUDA_VERSION 13000` |
| Installed compiler distribution | `nvidia-cuda-nvcc 13.4.92` |
| Installed CRT distribution | `nvidia-cuda-crt 13.4.92` |
| Runtime/header distribution | `nvidia-cuda-runtime 13.0.96` |
| Other CUDA distributions | CCCL 13.3.4.3.1, NVRTC 13.0.88, CUPTI 13.0.85 |
| PyTorch | 2.13.0; `torch/version.py` declares CUDA 13.0 |
| vLLM / FlashInfer | 0.30.0 / 0.6.18.post1 |
| `/usr/local/cuda*` paths | None |

The installed FlashInfer header lines 38–43 compare compiler major/minor to `(CUDART_VERSION / 1000, (CUDART_VERSION % 1000) / 10)`. Here that compares 13.4 against 13.0 and raises the exact observed error. Package RECORD files establish that nvcc is owned by the compiler distribution, runtime API headers by the runtime distribution, and `include/crt/host_config.h` by the CRT distribution. These are regular paths in one mixed installation, not a simple alternative system CUDA symlink.

The tracked recipe `am4-fleet-node/scripts/run-vllm-canary.sh` explicitly sets `CUDA_HOME`, `CUDA_PATH`, and its leading PATH entry to that pip CUDA prefix. A5's logged command confirms use of that prefix. Its `VLLM_USE_FLASHINFER_SAMPLER=0` does not avoid the observed FlashInfer attention JIT. The nvcc package metadata requires runtime/CRT/NVVM without version equality pins; that permits this mixed installation, but installation history was not inspected, so the cause of the package drift is unknown.

## Smallest evidenced correction

Align the JIT compiler and its compiler-side components with the existing CUDA 13.0 runtime/header stack and PyTorch build. Stage a coherent CUDA **13.0** compiler/CRT/NVVM toolchain in an isolated candidate environment or complete separate toolkit prefix. Pin its versions after resolving the available packages, and point the candidate recipe's CUDA_HOME/CUDA_PATH/PATH consistently to that prefix. Do not change the accepted A4 environment in place. A path-only correction is not currently evidenced: no alternative complete toolkit was found under `/usr/local`.

Downgrading only the nvcc executable while retaining 13.4 CRT/NVVM is not a demonstrated fix. Upgrading the whole runtime to 13.4 would be a broader stack change than matching the already working CUDA 13.0 runtime. Exact 13.0 compiler wheel patch versions/availability and the complete dependency resolution remain to be checked; this diagnosis supplies no guessed installation command.

Keep the compatibility check enabled. Defining `CCCL_DISABLE_CTK_COMPATIBILITY_CHECK`, editing version macros, or changing attention backend to hide the compilation error would not establish the intended A5 FP8 arm under the repaired environment.

Before another model load, the integration owner should review the isolated dependency change, verify compiler/header major/minor equality and CRT/NVVM coherence, and run a bounded standalone compilation of the affected FlashInfer operation in the candidate environment. Compilation would write a new isolated cache and requires a separately authorized follow-up; it was not done here. Then rerun A5 with the campaign's sole-writer, zero-lease, guard, backup, bounded-startup and automatic-restore protocol. Use a new evidence directory and record toolchain changes alongside the unchanged FP8 flag. If JIT passes, still measure readiness, quality and resource behavior before an FP8 verdict.

## Reproducible read-only version checks

The alias `am4` failed host-key verification; the plan's existing `10.44.0.2` target worked with normal verification. No verification bypass was used. Safe narrow probes (no environment or credential dump):

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 10.44.0.2 \
  '/home/derek/.venvs/vllm-cuda-030/lib/python3.12/site-packages/nvidia/cu13/bin/nvcc --version'
ssh -o BatchMode=yes -o ConnectTimeout=10 10.44.0.2 \
  'grep "^#define CUDART_VERSION" /home/derek/.venvs/vllm-cuda-030/lib/python3.12/site-packages/nvidia/cu13/include/cuda_runtime_api.h'
```

No external compatibility matrix was needed: the installed compiler, owning package records, runtime version macros, exact JIT command and failing source check directly establish this mismatch. Repairing it removes this known blocker; it does not promise the next build or the FP8 quality measurement will pass.

## Authorized isolated CUDA 13.0 staging — compile blocked

The integration owner authorized package staging and a minimal compile after the read-only diagnosis (estimate 5–10 minutes). Five official PyPI wheels were downloaded through the version-specific JSON API, checked against its SHA256 values, and extracted without package installation to AM4 `/home/derek/bench27-cuda130/toolkit`. Existing packages, system files, recipes and services were untouched. The owning manifest and failed probe are copied into `docs/bench27-cuda130-staging/`.

| Distribution | Staged version | Verified wheel SHA256 |
|---|---|---|
| nvidia-cuda-nvcc | 13.0.88 | 56fe502eb77625a12f25172caa3cdddb4e4c8ba2c8c17dba44b164761b380f03 |
| nvidia-cuda-crt | 13.0.88 | 2c8043c7c9e02492716426e9919fc78d2c5b3b2a7a768a88e952676b08aa55a4 |
| nvidia-nvvm | 13.0.88 | c5f41ffeb6466944a026dfa5317d7d85355c119bbec279205d22f1869d1054e0 |
| nvidia-cuda-runtime | 13.0.96 | 7f82250d7782aa23b6cfe765ecc7db554bd3c2870c43f3d1821f1d18aebf0548 |
| nvidia-cuda-cccl | 13.0.85 | e0da7ad981f3a8aff08241b5bfc1af868742a63e2762f53a5171c492ef242649 |

The staged nvcc reports 13.0.88 and staged runtime header reports 13000. A minimal CUDA kernel including the **same installed FlashInfer CCCL header** was compiled for both `sm_89` and `sm_120f`, without running it. Exact argv and stderr are preserved in `probe-result.json`. Result: exit 2. The original version check is no longer the error; CUDA 13.0 CRT `math_functions.h:629,653` declarations for rsqrt/rsqrtf lack exception specifications and conflict with glibc's noexcept declarations in `bits/mathcalls.h:206`. Host probes report GCC 15.2.0, glibc 2.43, and only GCC/G++ 15 under `/usr/bin`. Existing CUDA 13.4 CRT adds `_NV_RSQRT_SPECIFIER` to these declarations. No header edits or compatibility bypass were attempted.

Thus a coherent CUDA 13.0 toolkit alone is insufficient on this host. A clean 13.0 route would also need a compatible isolated host toolchain/sysroot. `candidate.patch` changes CUDA_HOME only and is marked **blocked_do_not_apply**. The live recipe advanced under the sole campaign owner while this draft was collected; its captured control SHA is in `candidate-status.json`, and should not be presumed to be an earlier fixed A4 control. Rebase and hash-check any eventual candidate against the owner's frozen control.

## Authorized isolated CUDA 13.4 staging — compile passes, model untested

After the 13.0 failure the integration owner authorized a coherent 13.4 JIT staging follow-up (estimate 3–5 minutes). Official PyPI wheels were hash-verified and extracted to AM4 `/home/derek/bench27-cuda134/toolkit`, again without installing packages into the existing venv. `docs/bench27-cuda134-staging/manifest.json` records exact URLs, versions, wheel names, dependency metadata and SHA256 values:

| Distribution | Version | Verified wheel SHA256 |
|---|---|---|
| nvidia-cuda-nvcc | 13.4.92 | a1f3bfb27299e060b444d5df1f4bcd762501326cf0fd3141ed61756816ab9a0e |
| nvidia-cuda-crt | 13.4.92 | 731ce3de11df8add8306404417a1edaede225dbe27eb233aed37c8de9d4fe720 |
| nvidia-nvvm | 13.4.92 | e4c81cb321dd9743bcf871c5e7e8fecd7f05dea607944426e102bdda8d49e137 |
| nvidia-cuda-runtime | 13.4.92 | 9641f797da20ce1dd8e779b6e96d08cf9ba564cec8e8225458811ee26423f3a5 |
| nvidia-cuda-cccl | 13.3.4.3.1 | 48ba8e44a1162face51306d5bfd67604d9949be5acb9105dbb93734d2df8b3a4 |

CCCL has its own package version cadence; the JIT uses FlashInfer's bundled CCCL include paths from the original failing command. Compiler, CRT, NVVM and runtime/header packages now all have version 13.4.92.

Two compile-only probes passed (exit 0, no stderr):

1. The same minimal kernel and installed FlashInfer CCCL include used in the failed 13.0 attempt, targeting both `sm_89` and `sm_120f`.
2. The actual A5 generated `batch_prefill.cu` translation unit from log line 180, preserving flags/include order/targets, changing only the CUDA toolkit prefix and directing object/dependency outputs into the isolated staging directory. Exact argv and source hash are in `fp8-compile-result.json`. This is one translation unit, not the full 11-unit extension link, runtime import or inference.

No GPU kernel was launched. Neither successful compilation proves CUDA 13.4 JIT artifacts work correctly inside a PyTorch build targeting CUDA 13.0. Existing runtime libraries remain unchanged; the draft sets no LD_LIBRARY_PATH and does not replace existing venv packages. Link/load behavior, runtime ABI compatibility, numerical behavior and performance remain unmeasured. This is an **awaiting-review environment arm**, not an FP8 repair verdict.

### Exact candidate and review gates

The remote and local `candidate-script.sh` changes only:

```bash
export CUDA_HOME=/home/derek/bench27-cuda134/toolkit/nvidia/cu13
export FLASHINFER_WORKSPACE_BASE=/home/derek/bench27-cuda134/jit-workspace
```

Existing CUDA_PATH/PATH assignments follow CUDA_HOME. The new FlashInfer cache root is supported by the installed `flashinfer/jit/env.py:59`; `flashinfer/jit/cpp_ext.py:49` reads CUDA_HOME/CUDA_PATH. Isolating its cache avoids using or overwriting the prior JIT cache. Inference argv is unchanged from the captured control. Captured control SHA256 is `4b2e241d13614c9ae0069dfc47aef4ca73d608ddeb91bafd49d7f5d3543be6a4`; candidate SHA256 is `1343633c1109f2abb32398c577f356ad6a5b72b775989aa4ec8520b5485b6316`. The owner was actively advancing context arms; these bytes must be reconciled with the selected frozen control before application.

Read-only review commands:

```bash
ssh -o BatchMode=yes 10.44.0.2 'sha256sum /home/derek/run-vllm-canary.sh /home/derek/bench27-cuda134/control-script.sh /home/derek/bench27-cuda134/candidate-script.sh'
ssh -o BatchMode=yes 10.44.0.2 'cat /home/derek/bench27-cuda134/candidate.patch /home/derek/bench27-cuda134/candidate-status.json'
```

The owner must obtain independent Opus review before applying this candidate. After review, use the existing guarded recipe swap/load/restore procedure and new evidence directories: first run the **same accepted non-FP8 baseline probes under the new toolchain environment**, then prepare a separately recorded FP8 retry. Do not combine the environment change with FP8 and attribute all effects to KV dtype. Preserve the original A5 and failed 13.0 records. No service or model action has been taken by this builder.

For the approvals row: isolated package downloads/extraction and two compile-only probes were authorized enabling-ladder work; live mutation remains solely the integration owner's reviewed step. The exact compile commands are executable JSON argv arrays in the saved probe result files, avoiding shell reconstruction. Source files, wheels, objects and records remain only in the two remote staging roots; copied review records are in this isolated worktree.
