# zen-deepseek-node

**STATUS (2026-07-03): PIVOTED.** This was built to run DeepSeek via the
opencode Zen free tier. Zen's free tier started requiring a card on file
before granting access, so the already-provisioned node was repointed at
OMEN's own local Ollama MoE (`mixtral:8x22b-instruct-v0.1-q2_K`, see
`C:\work\tuning\ollama-moe-findings.md` on OMEN) instead — sunk local
compute, no metered spend, no card required. Steps 1–2 below (clone,
generalize, reuse fleet-worker-node) still happened exactly as written;
step 3 (opencode + Zen key) did **not** — see "Pivot: OMEN local MoE
instead of Zen" below for what actually shipped. The directory name is now
a historical artifact, not a current description — a rename is optional
future cleanup, not required for this to work.

An `exploratory-perspective` fleet worker: same GPU-agnostic template as
`fleet-worker-node`. Purpose is to widen the planner/critic loop with an
extra perspective — not to sit on any critical-path pool. See
[`node.json`](node.json) for the manifest.

## 1. Clone the golden base

Same base image as every other worker (`claudefarm1` snapshot). From the
Hyper-V host:

```powershell
# clone the claudefarm1 golden-base snapshot to a new VM/VHD
# (name it zen-deepseek-1, do not reuse claudefarm1's VM directly)
```

Boot the clone, then **generalize it** before it touches the network —
skipping this makes it collide with claudefarm1 on the tailnet/LAN:

```bash
# new machine-id
sudo rm -f /etc/machine-id /var/lib/dbus/machine-id
sudo systemd-machine-id-setup

# new SSH host keys
sudo rm -f /etc/ssh/ssh_host_*
sudo ssh-keygen -A

# new hostname
sudo hostnamectl set-hostname zen-deepseek-1
```

## 2. Reuse fleet-worker-node as-is

No script changes needed — it's already GPU-agnostic:

```bash
git clone <commandcenter-repo> ~/commandcenter-src   # read-only source mirror, matches other workers
cp -r ~/commandcenter-src/fleet-worker-node ~/fleet-worker-node
cp -r ~/commandcenter-src/zen-deepseek-node ~/zen-deepseek-node

python3 -m venv ~/fleet-worker-node/.venv
~/fleet-worker-node/.venv/bin/python -m pip install "mcp==1.28.1"
chmod +x ~/zen-deepseek-node/scripts/zen-quota-probe.sh
```

## 3. Pivot: OMEN local MoE instead of Zen

Steps 3–5 as originally written (opencode install, Zen auth login,
`ZEN_DEEPSEEK_API_KEY`) are **superseded** — Zen's free tier now requires a
card on file. What actually shipped instead, reusing the exact same
provisioned VM:

1. Confirm the node can reach OMEN's Ollama directly (mshome.net sibling
   DNS, no tailnet needed on the VM — same addressing pattern as
   `omen-worker-1`):
   ```bash
   curl -s http://omen.mshome.net:11434/api/tags | head -c 500
   ```
2. Point `~/fleet-worker-node/runner.json` (agent-agnostic per-node runner
   config, read by `worker-mcp-server.py`) at the model:
   ```bash
   cat > ~/fleet-worker-node/runner.json << 'EOF'
   {"runner":"openai","base_url":"http://omen.mshome.net:11434/v1","model":"mixtral:8x22b-instruct-v0.1-q2_K","token_file":"","max_steps":24}
   EOF
   ```
3. Sanity-check the completion path end to end (cold load is slow —
   22–40s per `ollama-moe-findings.md` — give it a real timeout):
   ```bash
   curl -s -m 80 http://omen.mshome.net:11434/v1/chat/completions \
     -H 'Content-Type: application/json' \
     -d '{"model":"mixtral:8x22b-instruct-v0.1-q2_K","messages":[{"role":"user","content":"Reply with exactly the word: PONG"}],"stream":false,"max_tokens":10}'
   ```

No `opencode` install, no API key, no quota probe needed — this is sunk
local compute on OMEN, not a metered API.

## 4. Fill in node.json

Set `host`/`access` to the clone's actual IP (Hyper-V Default Switch leases
can move across host reboots — same caveat as every other worker), and set
`model_provider` to the `ollama-openai-compat` block shown in
[`node.json`](node.json).

## 5. Register with the conductor and verify

Register (adds the entry to the conductor's `fleet.json`):
```bash
python3 scripts/register_node.py --name zen-deepseek-1 --tailnet <ip> \
  --manifest fleet-worker-node/zen-deepseek-1.json --role worker,exploratory-perspective
```

Then hand-add two fields the script doesn't set (same pattern as
`claudefarm1`'s entry) and commit immediately (another agent may be
committing on the conductor concurrently):
```json
"exclude_from_build_pool": true,
"dispatch_pool": "exploratory-perspective"
```

Verify over the real MCP transport:
```bash
python3 fleet-worker-node/scripts/mcp_call.py \
  --ssh "ssh -i $HOME/.ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new claude@<zen-deepseek-1-ip>" \
  --python /home/claude/fleet-worker-node/.venv/bin/python \
  --server /home/claude/fleet-worker-node/scripts/worker-mcp-server.py \
  --tool node_status
```
Run this with the venv's own python if the conductor's bare `python3` lacks
the `mcp` package: `./fleet-worker-node/.venv/bin/python fleet-worker-node/scripts/mcp_call.py ...`.

Sign-off: `node_status` and `check_claude_auth` both return over MCP, same
contract as every other worker.

## Why this stays off the critical path

This node's real pool-scoping mechanism is `fleet.json`'s
`exclude_from_build_pool: true` — **not** the `dispatch_pool` field.
`conductor_maf.py` (the live conductor) has no concept of `dispatch_pool` at
all; that field is only read by this repo's local test-fixture scheduler
(`tools/workflow/reference_runner.py`). With `exclude_from_build_pool: true`,
`load_nodes()` never selects this node by default — it's only dispatched to
via a per-request `CCMETA` `builders` allow-list (see
`docs/conductor-pour-howto.md`).

The model itself is also just slow (~4.2–4.4 tok/s, per
`ollama-moe-findings.md`) and Q2_K-quantized — an overnight/second-pass
critic, not an interactive or high-trust final arbiter. Both facts point the
same direction: exploratory-perspective only, by design and by mechanism.
