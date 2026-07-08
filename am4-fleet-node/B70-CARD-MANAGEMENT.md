# AM4 B70 card management — runbook

How to run and manage the two Intel Arc Pro **B70** (Battlemage) GPUs on **AM4**, learned the
hard way 2026-07-05/06. AM4 is **native Ubuntu** now (it *was* Windows — the `vllama.exe`/`D:\work`
heritage in old docs is dead; only the config contract survived). SSH in as `derek@100.116.82.60`
(or `am4.tail8e749c.ts.net`).

## Hardware & layout

- **2× B70, 32 GB VRAM each** (30.3 GiB usable), SYCL device ids `SYCL0` (card 0) / `SYCL1` (card 1).
- **4 TB drive** = `/dev/nvme1n1p2` mounted at `/mnt/win` (the old Windows disk, read-only-ish).
  Windows-era trees survive at `/mnt/win/work/{vllama,b70tools,battlemage}` — reference only.
- **Model ggufs:** `~/baseline/models/` (Qwen3-30B-A3B, Mistral-Small-24B, Qwen2.5-32B) and
  `/mnt/win/work/battlemage/models/` (Qwen2.5-14B). `llama-server` (SYCL build) at
  `~/baseline/llama.cpp/build-sycl/bin/llama-server`.

## The serving stack

```
facade :8090  (oxen-facade.py, systemd SYSTEM svc am4-oxen-facade, Bearer AM4_OXEN_TOKEN)
  ├─ oxen-planner / vllama-planner → 127.0.0.1:8080  (card 0)
  └─ oxen-critic  / vllama-critic  → 127.0.0.1:8081  (card 1)
```
Aliases: `~/.config/am4-fleet/alias-backends.json` (a verbatim carry-over of vllama's
`/mnt/win/work/vllama/config/slots.json`). Facade env: `~/.config/am4-fleet/oxen.env`.
The **designed** layout is one model per card: planner on card 0, critic on card 1.

## Capacity: models fit on ONE card — dual-split is for speed, not fit

Qwen3-30B-A3B Q4_K_M weights are ~18.5 GB → fits one 32 GB card easily at ≤32k ctx. The
`relaunch-qwen3-baseline.sh` `-dev SYCL0,SYCL1` **dual-split is a throughput choice** (tensor-parallel
~81 tok/s), *not* a capacity requirement. For a **planner↔critic loop, prefer single-card-per-model**
(one card each) so both roles are resident. Per-card fit of the models on disk:

| Model | ~VRAM | Card fit |
| --- | --- | --- |
| Qwen2.5-14B Q4 | 8.4 GB | single, easy (the critic) |
| Mistral-Small-24B Q4 | 13.3 GB | single |
| Qwen3-30B-A3B Q4 | 18.5 GB | single (≤32k ctx); dual only for speed (the planner) |
| Qwen2.5-32B Q4 | 19.9 GB | single (dense; dual for speed) |

## Host RAM is the binding constraint — NOT VRAM (32 GB DDR4)

VRAM is plentiful (2×32 GB). The real limit is the **32 GB DDR4 host RAM**. SYCL `llama-server`
keeps host-side buffers (KV cache host copies, compute buffers) even with `-ngl 99`, plus the mmap'd
gguf in page cache. Co-loading the 30B planner + 14B critic at generous contexts **OOM-killed the
planner** mid-sweep (2026-07-06: `b70-planner.service: Failed with result 'oom-kill'` → 503s on every
in-flight request until systemd restarted it ~20 s later).

Measured on this box: critic at **32k ctx = 7.8 GiB host RSS**; dropping it to **8k ctx freed ~7 GiB**
(used 23→16 GiB, available 6→13 GiB). Rules of thumb:
- Keep contexts small: **critic ≤ 8k, planner ≤ 16k** for planning workloads (both slots resident).
- Watch `free -h` — keep **>8 GiB available** under load; two big models co-resident is tight.
- **Preflight gate (implemented):** `scripts/b70-preflight.sh <max_used_gib> <label>` is a Linux port of
  vllama's `max_host_used_gb_preflight`. Each `serve-*.sh` calls it before `exec` with a **used-ceiling of
  27 GiB** — refuse to start only when host RAM is ALREADY near-full, so we don't pile onto an exhausted
  box. Test: `bash ~/baseline/b70-preflight.sh 5 test` (low ceiling) → refuses (exit 3).
  - ⚠ **It MUST be a used-ceiling, not an available-floor.** An available-floor design (require N GiB
    `MemAvailable`) **crash-loops**: two legitimately co-loaded models keep `MemAvailable` low, so the gate
    refuses their own systemd restarts (`status=3` loop → facade 502s). Cost us a whole sweep 2026-07-06.
  - The gate guards *launch-ordering* (don't add a 3rd model to a full box); it does **not** prevent a
    *runtime* OOM peak — that's what small contexts are for, and ultimately **30B + 14B co-resident is
    marginal on 32 GB**. For a long/large sweep, prefer ONE model on AM4 (cross-machine: AM4 planner +
    OMEN critic) rather than co-loading both.
- OOM signature: `journalctl --user -u b70-planner | grep -i oom-kill`. Gate-refusal loop signature:
  `journalctl --user -u b70-planner | grep NOTIMPLEMENTED` (status=3 = the preflight exit).

## Launch recipe (SYCL llama-server)

**Always `source /opt/intel/oneapi/setvars.sh` first** — the SYCL build needs the oneAPI runtime
(`libsvml.so` etc.) on the library path, or it won't start. Single-card:

```bash
source /opt/intel/oneapi/setvars.sh >/dev/null 2>&1
llama-server -m <gguf> -ngl 99 -dev SYCL0 -fa on -fit off \
  -ctk q8_0 -ctv q8_0 -c 16384 -np 1 --threads 12 --host 127.0.0.1 --port 8080
```
Dual-card (throughput): `-dev SYCL0,SYCL1 -sm layer -ts 1,1`. Keep `-c` modest (16k–32k) for
planning; **131072 ctx blows up the KV cache** (q8_0 KV at 131k ≈ 6 GB) — the auto-`np=4 × 128k`
KV blowup was the 2026-06-17 planner-503 incident (see vllama ADR-0007). Raise ctx deliberately.

## Persistence — systemd `--user` units (survive logout AND reboot)

`loginctl enable-linger derek` is **on**, so user services persist. The managed units:

```
~/baseline/serve-planner.sh   → b70-planner.service   (Qwen3-30B, SYCL0, :8080)
~/baseline/serve-critic.sh    → b70-critic.service    (Qwen2.5-14B, SYCL1, :8081)
```
Manage them:
```bash
systemctl --user status  b70-planner b70-critic
systemctl --user restart b70-planner            # reload a card
systemctl --user stop    b70-critic             # free card 1 (e.g. for imagegen)
systemctl --user enable --now b70-planner b70-critic   # (re)install + start; enabled = starts on boot
```
For a one-off ad-hoc server use `systemd-run --user --unit=NAME --collect bash <script>` — it
returns cleanly and persists. **Do NOT** rely on `nohup … &` over SSH (see gotchas).

⚠ **Zombie units (disabled 2026-07-07):** `am4-planner.service` / `am4-critic.service` (an older
hermes-era pair in `~/.config/systemd/user/`) pointed at a deleted `start-hermes-backend.sh` and
crash-looped **58,922 restarts** (`Restart=always`, every 5 s, ~3.4 days) before being
`systemctl --user disable --now`'d. Unit files left in place for reference. Lesson: no latent
second claimant for :8080/:8081 — `b70-planner`/`b70-critic` are THE managed units.

## Waking from HEARTH (`wake_am4`)

`hearth/toolsurface/summon.py:wake_am4` (the `revive` hook for the `am4-oxen` backend in
`hearth/etc/backends.toml`) is LIVE and is the sanctioned remote wake path:

1. **Idempotent:** facade `http://100.116.82.60:8090/health` → `backend.ok` true = no-op.
2. **Occupancy-gated:** if imagegen (ComfyUI/python) holds the render nodes it checks the
   ComfyUI queue (`:8188/queue`) — only an in-flight or unverifiable job refuses the wake
   (`force=True` overrides). Idle ComfyUI merely resident does NOT block (it holds the
   nodes 24/7); our own `llama` slot holders never block either.
3. **Wakes the managed unit:** `systemctl --user start b70-planner` over SSH (never
   nohup — gotcha #2), then polls facade health until `backend.ok` or `wait_s` elapses.

The dual-card `~/baseline/relaunch-qwen3-baseline.sh` (SYCL0,SYCL1 split, 131k ctx) is the
*experiment throughput mode*, not the wake target — it grabs both cards (stomps the critic
slot and imagegen). Run it ad hoc via `systemd-run --user` when a matrix sweep wants
tensor-parallel speed, and stop `b70-planner`/`b70-critic` first. No permanent unit wraps it,
deliberately: a latent second claimant of :8080 is how the zombie-unit crash-loop happened.

## Gotchas (each cost real time this session)

1. **Self-killing `pkill`.** NEVER run `pkill -f llama-server` inside an SSH command: `-f` matches
   the *full command line*, and your own remote shell's command line contains the string
   "llama-server" (from the pkill argument), so it **kills its own session** mid-command — silent,
   partial execution, no output. Use `systemctl --user stop b70-*` or `pkill -x llama-server`
   (exact process-name match).
2. **SSH background detach swallows output / may not persist.** Spawning a long-lived GPU process
   with `nohup … &` over SSH often returns nothing and can die on session close. Use `systemd-run
   --user` or an installed `--user` unit, and **verify with a separate poll**, not the launch call.
3. **CRLF line endings.** Scripts synced from the Windows era have CRLF → `set -eo pipefail` fails
   with `set: pipefail: invalid option name`. Ship scripts via `base64 -d` (LF-clean) or
   `sed 's/\r$//'`.
4. **oneAPI env is mandatory** before launching the SYCL server (point 1 of the recipe).

## Imagegen coexistence

ComfyUI (`:8188`) shares the B70s — it holds `/dev/dri/renderD128`/`renderD129` even when idle.
Check it's idle before loading LLMs: `curl -s localhost:8188/queue` → `queue_running:[]`,
`queue_pending:[]` means idle. **Two LLM cards leave none for imagegen** — free one with
`systemctl --user stop b70-critic`. AM4 is rock-solid under load (1000 imagegen jobs / 7 h / 92 °C).

## Health / inspection

```bash
curl -s :8080/health ; curl -s :8081/health            # per-slot serve-truth
curl -s :8090/v1/models -H "Authorization: Bearer $AM4_OXEN_TOKEN"   # facade aliases + ready
clinfo | grep -iE 'Device Name|Global memory'          # card totals
tr '\0' ' ' < /proc/$(pgrep -x llama-server)/cmdline   # which -dev SYCL<n> a server uses
# per-card free VRAM appears in the llama-server startup log: "SYCL0 … MiB free"
```
`AM4_OXEN_TOKEN` lives in OMEN `hearth/var/gateway.cmd` and AM4 `~/.config/am4-fleet/oxen.env`.
