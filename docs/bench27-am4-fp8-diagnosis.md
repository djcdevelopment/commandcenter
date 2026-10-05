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
