"""LZ3 D2D asymmetry re-test: ABAB-interleaved staged copies between the two B70s.

Run from the torch-xpu venv: E:\\work\\xpu-train\\.venv\\Scripts\\python.exe lz3_d2d.py
Prior single-run measurement showed 2.29 (0->1) vs 5.05 (1->0) GB/s; this decides
whether that asymmetry is real or a warm-up/order artifact.
"""
import json
import time

import torch

SIZES = [64 * 2**20, 256 * 2**20, 2**30]


def timed_copy(dst, src, nbytes):
    torch.xpu.synchronize(0)
    torch.xpu.synchronize(1)
    t0 = time.perf_counter()
    dst.copy_(src)
    torch.xpu.synchronize(0)
    torch.xpu.synchronize(1)
    return nbytes / (time.perf_counter() - t0) / 1e9


for n in SIZES:
    d0 = torch.empty(n, dtype=torch.uint8, device="xpu:0")
    d1 = torch.empty(n, dtype=torch.uint8, device="xpu:1")
    # warm-up both directions before any timing
    d1.copy_(d0)
    d0.copy_(d1)
    torch.xpu.synchronize(0)
    torch.xpu.synchronize(1)
    a, b = [], []
    for _ in range(5):  # ABAB interleaved, never blocked per direction
        a.append(round(timed_copy(d1, d0, n), 2))
        b.append(round(timed_copy(d0, d1, n), 2))
    print(json.dumps({"size_mib": n // 2**20,
                      "gbps_0to1": a, "gbps_1to0": b}))
    del d0, d1
    torch.xpu.empty_cache()
