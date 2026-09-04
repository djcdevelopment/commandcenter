# fleet/arcserve — what runs on OMEN's B70s, and how

Since **2026-09-03 12:45:02–12:45:25** (window `rot-cutover-20260903-1245`, 23 s, closed `status: done` in
`hearth/var/rotation-windows.jsonl`; commit `26a1d66`; [ADR-0045](../../docs/adr/0045-the-scheduler-plans-a-rotating-host.md) decision 3)
the two Arc Pro B70s are served by **llama-swap v251 on `127.0.0.1:8081`**, which owns the process
lifecycle. Production (`qwen3-30b-a3b`, the door's `omen-arc` rung) still answers on **`:8082`** —
under llama-swap, through a per-model proxy — so nothing that talked to `:8082` before the cutover
had to change. Side models (the `omen-swap` rung) load on demand beside it.

## Files

| file | role |
|---|---|
| `serve-arc.cmd` | **the launcher.** Run by the `ArcServeBoot` scheduled task (S4U, boot trigger, `ExecutionTimeLimit PT0S`, `RestartCount 3 @ PT1M`). Since the cutover it is byte-identical to `serve-arc-swap.cmd` (its header still names itself `serve-arc-swap.cmd` and says `PARKED`: the ceremony copied the parked file verbatim). |
| `llama-swap/omen.yaml` | every model entry, group and hook llama-swap knows about. **Activates at the next ArcServe restart, never by editing it** — a running llama-swap keeps the entries it was started with. |
| `warm-swap.ps1` | the post-launch warm step: waits for `GET :8081/upstream/qwen3-30b-a3b/health` = 200 (240 s deadline), fires one 1-token completion so the rung is warm before the fx99 keep-alive's next tick (ADR-0043). Logs to `hearth/var/arc-swap-warm.log`. Never restarts anything. |
| `restart-arc.cmd` | bounce/stop the whole tree. Invoked by the `ArcServeRestart` scheduled task (no trigger, RunLevel Highest, S4U): `schtasks /Run /TN ArcServeRestart` from a medium-integrity shell. |
| `serve-arc-direct.cmd` | **the rollback** — the pre-cutover launcher, body unchanged from commit `c6370b0`, launching `llama-server.exe` directly on `:8082`. |
| `serve-arc-swap.cmd` | the parked copy of the llama-swap launcher. `cutover.ps1 -Live` installs it over `serve-arc.cmd` as its step B, so an `ArcServeBoot` fired by any other lane (a reboot, imagegen recovery) could not cut over un-ceremonied. |
| `cutover.ps1` | the cutover ceremony: dry-run by default, `-Live` executes, rollback on any abort. |
| `warm-arc.ps1`, `arc-serviceability.ps1`, `serve-arc-oss.cmd` | predate the cutover and are not part of it (`warm-arc.ps1` is the fx99 keep-alive's warm probe against `:8082` and is one of the consumers the cutover kept byte-identical). |

## The launcher, step by step (`serve-arc.cmd`)

1. `call C:\work\commandcenter\hearth\var\gateway.cmd` — the gitignored token file the gateway
   also sources. It sets `OMEN_ARC_TOKEN`. **Never read or print that file.**
2. `set GGML_VK_MMV_MAX_COLS=16` (the 2026-08-24 crossover opt-in; inert at `-np 2`).
3. `set LLAMA_API_KEY=%OMEN_ARC_TOKEN%` — llama-server reads the api key from the environment, so
   the key never appears in the YAML and there is no `--api-key` flag anywhere on the llama-swap path
   (`serve-arc-direct.cmd` and `serve-arc-oss.cmd` still pass `--api-key %OMEN_ARC_TOKEN%` on the
   command line).
4. `start "" /B powershell ... -File fleet\arcserve\warm-swap.ps1` in the background.
5. Foreground: `E:\work\llama-swap-v251\llama-swap.exe -config fleet\arcserve\llama-swap\omen.yaml
   -listen 127.0.0.1:8081 > hearth\var\arc-swap.log 2>&1`. Foreground so the task stays `Running`
   and `schtasks /End` still tears the tree down.

## The production entry (why every `:8082` consumer is byte-identical)

`omen.yaml` entry `qwen3-30b-a3b`: `-m Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf --alias qwen3-30b-a3b
-ngl 99 -sm layer -ts 1,1 -fa on --no-mmap -dio -fit off -c 131072 -np 2 -ub 1024 --host 127.0.0.1
--port 8082 --slots --jinja --metrics -lv 5 --log-file hearth\var\arc-serve.log`, with
`proxy: http://127.0.0.1:8082`, `checkEndpoint: /health`, `ttl: 0`, `unloadTimeout: 60`. The port is
**fixed** (no `${PORT}`), the group `production` is `persistent: true, swap: false, exclusive: false`
(no other group can ever unload it), and `hooks.on_startup.preload` brings it resident at boot.

That is the whole trick: the door's `omen-arc` rung, the fx99 keep-alive (`warm-arc.ps1`),
`ff_ratecheck.py`, `occupancy.probe_omen_arc_slots` and the ETW/keep-alive readers all still speak
to `127.0.0.1:8082` with the same bearer and see the same server. **No device env on this entry**
(ADR-0042): ggml-vulkan selects by type and llama.cpp drops the iGPU; the cutover receipt read 2 B70
`using device` lines, 0 iGPU, 2 Vulkan model buffers, 0 CPU buffers, 49/49 layers.

Change `-c`/`-np` only in lockstep with `hearth/etc/backends.toml` `context_bytes`/`parallel_slots`.

## The side entries (the `omen-swap` rung)

Single-card models are declared **twice**, `<m>-vk1` (`GGML_VK_VISIBLE_DEVICES=1`) and `<m>-vk0`
(`GGML_VK_VISIBLE_DEVICES=0`), in one `swap: true` group per model, so the rotation lifecycle can
retry the sibling when placement disagrees:

| group | entries | `-c` |
|---|---|---|
| `phi4` | `phi4-vk1`, `phi4-vk0` (`phi-4-Q4_K_M.gguf`) | 8192 |
| `qwen14b` | `qwen14b-vk1`, `qwen14b-vk0` (`qwen2.5-14b-instruct-q4_K_M.gguf`) | 8192 |
| `gptoss20b` | `gptoss20b-vk1`, `gptoss20b-vk0` (`gpt-oss-20b-MXFP4.gguf`) | 4096 |
| `mistral24b` | `mistral24b-vk1`, `mistral24b-vk0` (`Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf`) | 4096 |
| `qwen27b-dual` | `qwen38-27b-dual` (`qwen38\Qwen3.8-27B-Q4_K_M.gguf`, `-sm layer -ts 1,1`, **no** device env, `-ub 1024`) | 32768 |

All side entries: `-ngl 99 -fa on --no-mmap -dio -fit off -np 1 --host 127.0.0.1 --port ${PORT}`
(`startPort: 18300`), `--slots --jinja -lv 5`, their **own** `--log-file
hearth/var/swap-logs/<id>.log`, and `--slot-save-path E:\work\battlemage\kv\` (llama-server refuses
to start without that directory — window `rot-side-20260903-A` died on exactly that; `039f68a`).

⚠ **Index 2 is the iGPU on this driver** (`hearth/var/swap-logs/phi4-vk2.log` line 6: `Vulkan0 :
Intel(R) Graphics`). The siblings were renamed `-vk2 → -vk0` in `92f3cd6`; those entries go live at
the next ArcServe restart, and until then env=1 puts every side model on BDF `0000:04:00.0`. Never
read a READY side server as correctly placed — its `-lv 5` report decides (`hearth/rotation/`).

## Restart, stop, rollback

`restart-arc.cmd` (via `ArcServeRestart`): `schtasks /End /TN ArcServeBoot`, `taskkill /IM
llama-swap.exe /T /F`, `taskkill /IM llama-server.exe /F`; **if `hearth\var\arc-maintenance.stop`
exists it exits here — stop-only** (that sentinel is shared with the imagegen lane's maintenance
path). Otherwise it polls up to ~120 s (60 × 2 s) until **both** images are gone, then `schtasks /Run
/TN ArcServeBoot`; if a process will not die it refuses to start a second instance (exit 1) rather
than race it on the port. Proven unattended: at 12:59 on cutover day the imagegen lane stopped
ArcServe through this file and its restore path booted production under llama-swap on its own —
verified 17:31, `at_rate` 107.3 tok/s (101% of the 106.0 baseline).

Rollback (`serve-arc-direct.cmd` header): copy `serve-arc-direct.cmd` over `serve-arc.cmd`, create
the sentinel, `schtasks /Run /TN ArcServeRestart`, delete the sentinel, `schtasks /Run /TN
ArcServeBoot`. `cutover.ps1`'s `Rollback` does exactly this and was exercised twice on 2026-09-03
(12:35 and 12:43, 36 s each).

## `cutover.ps1`

`powershell -NoProfile -ExecutionPolicy Bypass -File fleet\arcserve\cutover.ps1` is a **dry run**:
every pre-flight check runs read-only and each live step prints the command it would execute.
`-Live` refuses (exit 2) unless all **13 pre-flight checks** pass in the same invocation: rung state
`at_rate|warn`; no image session on `omen-b70-pool`; commit free ≥ 6 GB; keep-alive ticking (newest
row ok, ≤ 120 s old); `omen.yaml` present and free of key literals; llama-swap binary present;
`serve-arc-direct.cmd` present; `serve-arc-swap.cmd` present; `serve-arc.cmd` still the pre-cutover
launcher; `:8081` free; `:8082` listening; bearer available (never printed); sentinel absent.

Then, inside a `rot-cutover-<stamp>` window appended to `hearth/var/rotation-windows.jsonl`:
**A** stop the incumbent (sentinel + `ArcServeRestart`, wait for `:8082` closed and no llama-server,
150 s) · **B** copy `serve-arc-swap.cmd` over `serve-arc.cmd`, `ArcServeBoot`, wait `:8081/health`
(60 s), `:8082/health` (240 s), then a real 1-token completion with a `timings` block (120 s) ·
**C** assert dual-split placement from the server's own `--log-file` (last load report; read through
a `FileShare.ReadWrite` stream because the server holds the file open) · **D** a bare POST gets
401/403 · **E** `ff_ratecheck.py --rung omen-arc` exits 0 (the warm burst, ADR-0043 rule 1) ·
**F** a keep-alive row newer than the window start with `ok:true` (100 s) · **G** stamp
`epoch_boundaries[-1].ts` in `campaign/ff-probes/rate-baselines.json` (baseline 106.0 untouched).
Any failed step calls `Abort` → `Rollback` and closes the window `aborted`.

The two aborted attempts are why steps B/C look the way they do: at 12:35 the ceremony read
placement from llama-swap's `/logs`, which is a **~10 KB tail** — the `using device` lines had
scrolled out; at 12:43 `ReadAllText` on `arc-serve.log` took a sharing violation.

## Logs

- `hearth/var/arc-serve.log` — production's own `--log-file` (`-lv 5`; the load report lives here).
- `hearth/var/arc-swap.log` — llama-swap's stdout/stderr (`logToStdout: both`, so upstream output
  lands here too). `hearth/var/arc-swap-warm.log` — `warm-swap.ps1`.
- `hearth/var/swap-logs/<id>.log` — each side entry's own `--log-file`.
- `GET :8081/logs` — a ~10 KB tail. Fine for a glance, wrong for an assertion.

## Rules

- **Never bind llama-swap beyond loopback.** Its admin endpoints are unauthenticated. VM reach is an
  authenticated reverse proxy on `omen.mshome.net` forwarding `/v1/*` plus one firewall rule scoped to
  `vEthernet (Default Switch)` — registered in `DECISIONS-PENDING.md`, not built.
- **The bare `POST /api/models/unload` unloads EVERYTHING, production included.** Use the path form
  `POST /api/models/unload/{id}` only; `hearth/rotation/swapclient.py` names the bare form
  `unload_all()` so nobody reaches it by accident.
- Editing `omen.yaml` changes nothing until the next ArcServe restart.
- No secrets in the YAML or in any `.cmd`/`.ps1` here; under the llama-swap launcher the bearer travels
  only in the environment and in HTTP headers (the direct/oss launchers still pass it as
  `--api-key %OMEN_ARC_TOKEN%`).
- `ArcServeRestart` force-kills every `llama-server.exe` on OMEN, side models included; the door's
  `rotation_*` tools are the way to load and unload side models, inside a window.
