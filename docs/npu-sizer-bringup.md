# NPU sizer bring-up (ADR-0050, laps N1–N3) — omen-linux

The request sizer's `npu` mode runs MiniLM-L6 on OMEN's Arrow Lake NPU behind a loopback service.
The kernel side has been ready since the 26.04 install (`intel_vpu` 1.0.0 on 7.0.0-34,
`/dev/accel/accel0`, group `render`, firmware `vpu_37xx_v1.bin`); the user space is not. This page
is the exact sequence, split by who runs it. Everything not marked **sudo** was done by the session
that wrote this page (2026-09-28); the **sudo** block is Derek's.

## What is already in place (no sudo)

| item | where | state |
| --- | --- | --- |
| Intel linux-npu-driver v1.38.0 Ubuntu 26.04 bundle | `~/npu-driver/v1.38.0/*.deb` | downloaded 2026-09-28, three signatures verified good (release key `EA26 7657 A608 300C 296B 8F8A D52C 9665 A407 7678`) |
| Level Zero loader | `libze1 1.32.0-1~26.04~ppa1` (kobuk-team PPA) | installed; exactly the version 1.38.0 was verified against |
| `libtbb12` | `2022.3.0-2` | installed (a dependency of the compiler package) |
| venv | `~/.venvs/npu` (uv, CPython 3.12) | `openvino==2026.4.0`, `optimum-intel[openvino]`, `sentence-transformers`, `nncf` |
| encoder checkpoint | `/home/derek/models/all-MiniLM-L6-v2-1110a243fdf4706b3f48f1d95db1a4f5529b4d41/` | pinned snapshot (safetensors, tokenizer, 1_Pooling) |
| export / bench / serve tools | `tools/sizer/{export,bench,serve}.py` (this repo) | run under `~/.venvs/npu/bin/python` |

## Derek: the sudo block (one paste, ~1 minute)

The three packages are the matched set Intel verified on Ubuntu 26.04 + kernel 7.0 + libze 1.32.0
(release notes, 2026-09-11). `intel-fw-npu` installs to `/lib/firmware/updates/intel/vpu/`, which the
kernel searches before `/lib/firmware/`, so the distro's older firmware (2026-02-19) is superseded
without a conflict; the module must reload to pick it up (nothing holds `/dev/accel/accel0` until the
service starts, so `rmmod` is safe).

```bash
cd ~/npu-driver/v1.38.0
sudo dpkg -i intel-driver-compiler-npu_*.deb intel-fw-npu_*.deb intel-level-zero-npu_*.deb
sudo rmmod intel_vpu && sudo modprobe intel_vpu
sudo dmesg | grep -i 'intel_vpu.*Firmware'      # expect version 20260820*...*6fc835a1920... (was 20260219*...*f693e2c0)
```

Rollback: `sudo dpkg --purge intel-driver-compiler-npu intel-fw-npu intel-level-zero-npu && sudo rmmod
intel_vpu && sudo modprobe intel_vpu` restores the distro firmware.

## Verification (no sudo; the session runs this after the block)

```bash
ls /usr/lib/x86_64-linux-gnu/libze_intel_npu.so* /usr/lib/x86_64-linux-gnu/libnpu_driver_compiler.so
~/.venvs/npu/bin/python -c "import openvino as ov; c=ov.Core(); print(c.available_devices); print(c.get_property('NPU','FULL_DEVICE_NAME'), c.get_property('NPU','NPU_DRIVER_VERSION'))"
~/.venvs/npu/bin/python tools/sizer/bench.py --device NPU --device CPU     # latency, busy_time delta, memory delta
```

A positive `npu_busy_time_us` delta (sysfs, readable as a user) proves the NPU executed; the
memory delta is the DDR the sizer costs. Failure modes and their fixes:

- `available_devices` lacks `NPU` → `ZE_ENABLE_LOADER_DEBUG_TRACE=1` shows whether the loader found
  `libze_intel_npu.so.1`; a stale loader needs `ZE_ENABLE_ALT_DRIVERS=libze_intel_npu.so.1`.
- `MAPPED_INFERENCE_VERSION is NOT compatible` → firmware/compiler mismatch: the module did not
  reload after `intel-fw-npu`, or the distro firmware is still the one loaded.
- compile hangs or eats RAM → the model is not static; `export.py` reshapes to `[1,256]` and refuses
  dynamic inputs. `NPU_COMPILER_TYPE=DRIVER` swaps the in-wheel compiler for the driver's.

## Then (laps N1–N3)

1. `tools/sizer/export.py` → `~/models/minilm-ov/{fp16,int8}/openvino_model.xml`, static `[1,256]`,
   cosine vs the torch reference > 0.99 on ten probe sentences.
2. `tools/sizer/bench.py` → the N1 table: compile cold / cached, per-call latency NPU vs CPU at
   `[1,256]` and `[1,512]`, `npu_memory_utilization` delta, service RSS. "CPU wins" is a valid answer.
3. `tools/sizer/serve.py --device NPU --fallback CPU` on `127.0.0.1:8797`; `~/.config/systemd/user/
   omen-sizer.service`; `HEARTH_SIZER=npu` in `hearth-ops.env` only after the N2 head shows lift on the
   out-of-campaign set and the N4 pour passes.
