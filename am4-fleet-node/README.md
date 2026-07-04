# AM4 Fleet Node

AM4 is the always-on services hub and dual-B70 inference node.

Stable SSH:

```bash
ssh derek@am4.tail8e749c.ts.net
```

## Current Role

- Observability: Jaeger on `:16686`, OTLP on `:4317`/`:4318`.
- Agent control surface: MCP over stdio, normally launched through SSH.
- Optional fleet bus: NATS JetStream on `:4222`, monitoring on `:8222`.
- oxen/OpenAI facade: `http://am4.tail8e749c.ts.net:8090/v1`.
- Heavy backend: Linux-native llama.cpp SYCL/Level Zero on loopback `127.0.0.1:8080`, launched by `am4-oxen-backend.service`.
- GPUs: two Intel Battlemage G31 / Arc Pro B70 devices, render nodes `/dev/dri/renderD128` and `/dev/dri/renderD129`.

## Communication Contract

Use MCP as the northbound agent interface.

MCP is the right first contract because commandcenter/Codex-style callers need to discover node state, read resources, and invoke explicit tools like `render_owners`, `oxen_ready`, `start_oxen_backend`, and `stop_oxen_backend`.

Keep NATS optional. It becomes useful when a run contract needs durable queueing, replayable events, fanout to multiple workers, or high-volume telemetry. It should not be required for a single agent asking AM4 what it can do.

MCP over SSH:

```bash
ssh derek@am4.tail8e749c.ts.net /home/derek/am4-fleet-node/.venv/bin/python /home/derek/am4-fleet-node/scripts/am4-mcp-server.py
```

## oxen Alias Contract

First slice exposes:

- `oxen-planner` -> Qwen3-30B-A3B GGUF through dual-B70 llama.cpp.
- `oxen` -> same backend alias for clients that expect a literal oxen model name.

The alias names match the oxen alias contract. `oxen-critic` should be added as a second resident slot only after the AM4 backend has proven stable under one dual-card planner model.

## Safety Rule

Do not start `am4-oxen-backend.service` while another process owns both render nodes unless you deliberately want GPU co-tenancy. Check first:

```bash
~/am4-fleet-node/scripts/am4-node-status.sh
```

If ComfyUI is holding both render nodes, leave the backend stopped. The facade can run safely and will report `ready=false` until the backend is started.

## Denning vs AM4 Linux

Denning measured the hard mode: Windows/VidMm, where the OS can move the live budget underneath an inference process and where card-to-card movement bounced through host memory. AM4 is now Ubuntu, so the control problem is easier in one crucial way: memory placement can be made explicit and pinned through the Linux/Level Zero/SYCL stack.

The immediate validation target is not "does local inference work"; it is narrower:
**does SYCL multi-card work correctly on Ubuntu AM4?** It failed on Windows 10, which is why the old stack pivoted to Vulkan there. Ubuntu is the attempt to run the path the ecosystem expects.

With 32 GB host DDR, do not make pooled/shared memory the plan. Treat host memory as a scarce control-plane/swap buffer, not an extension of VRAM. The first useful target is bounded per-device placement:

- keep the resident workload inside B70 VRAM;
- avoid host spill and host-bounced steady-state paths;
- run one oxen long-context backend before chasing multi-tenant fanout;
- add custom probes/tooling at whichever layer is lying, including driver/kernel work if the public stack does not expose the needed control.

Use Denning as the bounds ledger, not as the Linux control law:

- Keep: measured KV bytes/token, q8-vs-f16 quality/perf, restore-vs-reprefill economics, decode cliff observations.
- Re-test: peer-to-peer/card-to-card throughput, pinned allocation behavior, and whether the serving engine can deliberately place KV on one card and weights/compute on another.
- Drop as default assumption: Windows VidMm budget arbitration and involuntary demotion as the primary hazard on AM4.

## llama.cpp Placement Plan

Default service:

```bash
BACKEND_KIND=sycl
LLAMA_SERVER=/home/derek/baseline/llama.cpp/build-sycl/bin/llama-server
DEVICE_LIST=0,1
SPLIT_MODE=layer
TENSOR_SPLIT=1,1
CTX=131072
KV_TYPE_K=q8_0
KV_TYPE_V=q8_0
```

Placement experiments to run once ComfyUI is not occupying both render nodes:

1. `layer`: baseline; prove SYCL multi-card works at all.
2. `row` with `MAIN_GPU=0`, then `MAIN_GPU=1`: candidate for KV/intermediate on one B70 with row-split weights across both.
3. `tensor`: candidate if row is unstable or slow.
4. Only after those are measured: raise `CTX=262144`.

The acceptance criterion is not "starts." It is: both cards are used as intended, no host spill is visible, readiness can generate through the oxen alias, and throughput is good enough to justify the placement.

Do not pick a placement from a single small-context smoke. Run a context ladder because the fast setting at shallow context can be the slow setting at oxen-scale context:

```bash
~/am4-fleet-node/scripts/context-ladder-probe.sh
CONTEXTS="0 8192 16384 32768 65536 131072" MODES="single0 single1 layer" ~/am4-fleet-node/scripts/context-ladder-probe.sh
```

Only include unstable modes explicitly:

```bash
MODES="row0 row1 tensor" CONTEXTS="8192 16384 32768" ~/am4-fleet-node/scripts/context-ladder-probe.sh
```

Recorded benchmark artifact:

- [2026-06-29-sycl-context-ladder.md](/C:/work/commandcenter/am4-fleet-node/results/2026-06-29-sycl-context-ladder.md)

What that first ladder showed:

- SYCL multi-card `layer` works on Ubuntu AM4.
- For one-stream long-context work, `layer` did not beat single-card through `64k`.
- `row` segfaulted for both `main_gpu` variants at `16k`.
- `tensor` was not stable enough to treat as a candidate.

## Operator Commands

```bash
# AM4
~/am4-fleet-node/scripts/am4-node-status.sh
~/am4-fleet-node/scripts/am4-node-status.sh --xpu-smoke

sudo systemctl status am4-oxen-facade.service
sudo systemctl status am4-oxen-backend.service

# Start backend only when render nodes are free.
sudo systemctl start am4-oxen-backend.service

# Verify facade.
curl -s http://127.0.0.1:8090/health
curl -s http://127.0.0.1:8090/v1/models -H "Authorization: Bearer $(cat ~/.config/am4-fleet/oxen.token)"
curl -s http://127.0.0.1:8090/oxen/ready?alias=oxen-planner -H "Authorization: Bearer $(cat ~/.config/am4-fleet/oxen.token)"
```

## Files On AM4

- `~/am4-fleet-node/` - scripts, facade, service files.
- `~/am4-fleet-node/.venv/` - MCP server Python environment.
- `~/.config/am4-fleet/oxen.env` - local service config.
- `~/.config/am4-fleet/oxen.token` - HTTP bearer token.
- `~/.config/am4-fleet/nats.conf` - optional NATS server config with auth.
- `~/.config/am4-fleet/nats.env` - optional NATS client environment values.

## Install

Default install is MCP-first and does not start NATS:

```bash
~/am4-fleet-node/scripts/install-am4-fleet-node.sh
```

Add NATS only when the run/event bus actually needs durability or fanout:

```bash
~/am4-fleet-node/scripts/install-am4-fleet-node.sh --with-nats
```
