# Runbook: two-dense window (both OMEN B70 seats serve the 27B)

Prepared 2026-10-03 on branch `wp/two-dense`; not yet run. Timings marked (doc) come from the repo's records, (limit)
from a script ceiling; none is measured for this profile.

## Shape

| | `two-lane` (rest) | `two-dense` (window) |
|---|---|---|
| seat 0, card 0, :18091 | qwen3.8-27b, 65,536 ctx, 4 seqs (door leases 2) | unchanged |
| seat 1, card 1, :18092 | qwen3-30b-a3b, 40,960 ctx, 8 seqs | qwen3.8-27b, same recipe as seat 0 |
| router :18095 | seat 0 (`omen-dense-27b`) | unchanged |
| router :18090 | seat 1 (`omen-vllm`) | no server: HAProxy answers 503 |
| router :18096 | none | seat 1 (`omen-dense-27b-b`) |
| lab configuration | `day` | `two-dense` (needs omen profile `two-dense` + AM4 `tool-pair`) |

Weights: one read-only checkpoint dir, `/home/derek/models/qwen3.8-27b-gptq-int4-mtp` (19 GB), shared by both seats (no copy,
no per-seat path; `start-vllm-seat.sh` takes `OMEN_MODEL`). Both cards are Intel Arc Pro B70 (`xpu-smi discovery`, lspci G31
x2). `/home` has 166 GB free. Seat 1 gets the recipe as one drop-in, `stage9-two-dense.conf`, sorted after seat 1's own
`stage0-recipe.conf` / `max-model-len.conf` / `max-num-seqs.conf`, so every key it sets wins (incl. the empty
`OMEN_KV_CACHE_BYTES`, which removes the 30B's fixed 13.1 GB pool). The dry-run argv of seat 1 equals seat 0's except `--port`.

What the door refuses meanwhile: `omen-vllm` is `absent` under `two-dense`, so `backend="omen-vllm"` pins and the `fast`
local-work lane (and tag `default`/`code`/`research`/`reasoning`) are refused naming the configuration. Untagged calls
fall to the pool default `omen-vllm` (not absence-gated) and fail loudly at the router (503). `deep` lane and
`backend="omen-dense-27b"` work. `omen-dense-27b-b` is reachable by pin only (`omen-dense-27b` has no occupancy probe and wins the
tag walk first). Banked Fire briefs on lane `fast` get refused during the window.

## 0. Deploy (any time before; not a restart)

Orchestrator merges `wp/two-dense` into the flash checkout first (the door reads `host/lab-configurations.toml` from the
checkout, and `day` then carries `omen-dense-27b-b = absent`). Then, about 1 min:

```
R=~/work/commandcenter-linux-flash/host/omen-linux
cp $R/systemd/omen-vllm@1.service.d/stage9-two-dense.conf.staged ~/.config/systemd/user/omen-vllm@1.service.d/
cp $R/profiles/two-dense.json                 ~/.config/omen-vllm/profiles/
cp $R/config/haproxy.cfg.two-dense.staged     ~/.config/omen-vllm/
cp $R/config/lab-configurations.toml          ~/.config/omen-vllm/
cp $R/hearth-production/backends-linux.toml   ~/hearth-production/     # door re-reads the pool per call; no restart
cd ~/work/commandcenter-linux-flash && PYTHONPATH=$PWD ~/.venvs/hearth-private/bin/python tools/ops/host_config.py --check   # expect OK
PYTHONPATH=$PWD ~/.venvs/hearth-private/bin/python tools/ops/sizing_map.py --check                                              # expect 0 violations
```

`.staged` files are ignored by systemd; nothing is active yet.

## 1. Enter (about 5-15 min of wall clock; seat 1 is down for most of it)

1. Timing. `date -u`; `systemctl --user list-timers bankedfire-drain.timer`. Start 5-10 min after a tick (:02/:32) so the
   next tick is more than 20 min away.
2. Zero leases on both seats: `~/bin/omen-profile status` (both `running_reqs=0.0`), `~/.local/bin/omen-ai-mode status`,
   HEARTH `queue_status` shows nothing running or queued on omen-vllm / omen-dense-27b. The switch refuses itself on active
   jobs or a held `omen-b70-pool` tenancy. About 1 min.
3. `~/bin/omen-profile switch two-dense --dry-run` (about 5 s). Seat 1 argv must show `qwen3.8-27b`, `--max-model-len 65536`,
   `--max-num-seqs 4`, `--max-num-batched-tokens 8192`, MTP `num_speculative_tokens 1`, port 18092, no `--kv-cache-memory-bytes`.
4. `~/bin/omen-profile switch two-dense`. Seat 0 is unchanged (skipped); seat 1 gets the drop-in copied, daemon-reload,
   restart, `wait-vllm-seat.sh 1 900`. Load time: 31-36 s for a seat swap (doc: `hearth/etc/capabilities/omen-dense-27b.json`);
   ceiling 900 s (limit). It writes `~/.config/omen-vllm/profile` = `two-dense`; the door then reads configuration `two-dense`.
5. Router (about 5 s; only after seat 1 is healthy, because until then `:18090` still maps to seat 1):
   ```
   cp ~/.config/omen-vllm/haproxy.cfg.two-dense.staged ~/.config/omen-vllm/haproxy.cfg
   ~/opt/haproxy/usr/sbin/haproxy -c -f ~/.config/omen-vllm/haproxy.cfg && systemctl --user restart omen-vllm-router.service
   ```
6. Health check (about 1 min):
   ```
   ~/bin/omen-profile status                       # both seats serve qwen3.8-27b
   ~/bin/lab-config status                         # configuration two-dense
   set -a; . ~/.config/omen-vllm/omen-api.env; set +a
   for p in 18095 18096; do curl -s -H "Authorization: Bearer $VLLM_API_KEY" http://127.0.0.1:$p/v1/models | python3 -c 'import sys,json;print([m["id"] for m in json.load(sys.stdin)["data"]])'; done
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18090/health        # 503
   ```
   Then one `local_generate(backend="omen-dense-27b-b", max_tokens=16)` and read `backend` in the result.
   `sizing_map.py --check --live` is expected to flag the absent default rung `omen-vllm` and families that resolve to it
   (that is the window's shape, not a fault); it has no serving check for `omen-dense-27b-b`.

## 2. Leave (about 5-15 min)

1. Zero leases on both seats (as 1.2); no running work pinned to `omen-dense-27b-b`.
2. `rm ~/.config/systemd/user/omen-vllm@1.service.d/stage9-two-dense.conf` (the omen-profile tool does not remove a seat-1
   drop-in on the way out; without this step it restarts seat 1 still on the 27B, fails its model check and rolls back).
3. `~/bin/omen-profile switch two-lane --dry-run`, then `~/bin/omen-profile switch two-lane`. Seat 0 is skipped; seat 1
   restarts on the 30B (same wait as 1.4).
4. Restore the router, then verify:
   ```
   cp ~/work/commandcenter-linux-flash/host/omen-linux/config/haproxy.cfg ~/.config/omen-vllm/haproxy.cfg
   ~/opt/haproxy/usr/sbin/haproxy -c -f ~/.config/omen-vllm/haproxy.cfg && systemctl --user restart omen-vllm-router.service
   ~/bin/omen-profile status        # seat 1 qwen3-30b-a3b; configuration back to day
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18096/health   # connection refused (000)
   cd ~/work/commandcenter-linux-flash && PYTHONPATH=$PWD ~/.venvs/hearth-private/bin/python tools/ops/host_config.py --check   # OK, 0 extra
   ```
   Serve one `local_generate(backend="omen-vllm", max_tokens=16)`; read `backend` and the model in the result.

## 3. If seat 1 fails to load

- Nothing at the router or door has changed yet (step 1.5 comes after the health wait), so only seat 1 needs undoing.
- The tool rolls back itself on a failed wait: it restores the drop-in snapshot and restarts BOTH seats (each up to 900 s),
  so seat 0's 27B restarts too and its leases are lost; budget up to two seat loads. Exit 1 = rolled back; exit 2 = pool left
  fenced, then by hand: `rm .../omen-vllm@1.service.d/stage9-two-dense.conf`, `systemctl --user daemon-reload`,
  `systemctl --user restart omen-vllm@1.service`, `~/bin/wait-vllm-seat.sh 1 900`, and ask Derek about releasing the tenancy.
- Cause: `journalctl --user -u omen-vllm@1.service -n 80 --no-pager | grep -iE 'error|OOM|OUT_OF_RESOURCES'`. A device OOM at
  `OMEN_GPU_MEM_UTIL=0.90` is the first suspect; lower that one line in the active `stage9-two-dense.conf` (0.85) and retry once.
- Verify with `~/bin/omen-profile status` that seat 1 serves `qwen3-30b-a3b` and `profile` reads `two-lane`.
