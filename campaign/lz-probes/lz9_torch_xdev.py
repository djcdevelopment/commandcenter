"""LZ9-E6: what does PyTorch XPU / Unified Runtime actually do for a cross-card copy?

Runs `a.to('xpu:1')` and `b.copy_(a)` for xpu:0 -> xpu:1 and back, judges the bytes on the
destination card, and times 64 MiB so the path can be classified: ~6-7 GB/s = host-staged
(the August LZ3 figure), anything far above the wire constant = something else is going on.
Run inside E:\\omen\\tensor\\.venv (torch 2.14.0+xpu). No engine code is imported.
"""
import json
import sys
import time

import torch


def main() -> int:
    out = {"torch": torch.__version__, "devices": [torch.xpu.get_device_name(i) for i in range(torch.xpu.device_count())]}
    out["can_access_peer"] = {}
    for s, d in ((0, 1), (1, 0)):
        try:
            out["can_access_peer"][f"{s}->{d}"] = bool(torch.xpu.can_device_access_peer(s, d))
        except Exception as exc:  # noqa: BLE001
            out["can_access_peer"][f"{s}->{d}"] = f"exception: {exc}"
    pattern = torch.tensor([0xA5, 0x5A, 0xC3, 0x3C], dtype=torch.uint8)
    for src, dst in ((0, 1), (1, 0)):
        key = f"xpu:{src}->xpu:{dst}"
        row = {}
        try:
            n = 4096
            a = pattern.repeat(n // 4).to(f"xpu:{src}")
            torch.xpu.synchronize(src)
            b = a.to(f"xpu:{dst}")
            torch.xpu.synchronize(dst)
            row["to_4k"] = "PATTERN_ARRIVED" if torch.equal(b.cpu(), pattern.repeat(n // 4)) else "MISMATCH"
            c = torch.zeros(n, dtype=torch.uint8, device=f"xpu:{dst}")
            c.copy_(a)
            torch.xpu.synchronize(dst)
            row["copy__4k"] = "PATTERN_ARRIVED" if torch.equal(c.cpu(), pattern.repeat(n // 4)) else "MISMATCH"
            n = 64 * 1024 * 1024
            big = pattern.repeat(n // 4).to(f"xpu:{src}")
            dstbuf = torch.empty(n, dtype=torch.uint8, device=f"xpu:{dst}")
            torch.xpu.synchronize(src); torch.xpu.synchronize(dst)
            dstbuf.copy_(big); torch.xpu.synchronize(dst)  # warm
            times = []
            for _ in range(5):
                torch.xpu.synchronize(src); torch.xpu.synchronize(dst)
                t0 = time.perf_counter()
                dstbuf.copy_(big)
                torch.xpu.synchronize(dst)
                times.append(time.perf_counter() - t0)
            best = min(times)
            row["copy__64MiB_best_s"] = round(best, 5)
            row["copy__64MiB_gbps"] = round(n / best / 1e9, 2)
            row["copy__64MiB_ok"] = bool(torch.equal(dstbuf[:4096].cpu(), pattern.repeat(1024)))
        except Exception as exc:  # noqa: BLE001
            row["exception"] = f"{type(exc).__name__}: {exc}"
        out[key] = row
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
