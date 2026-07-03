# fleet-worker-node

Reusable, GPU-agnostic **worker** template for the commandcenter fleet — the
worker counterpart to `am4-fleet-node`. A worker exposes an MCP control surface
that a conductor (OMEN) discovers and drives. First instance: `claudefarm1`, the
OMEN-local Hyper-V VM (slice S0).

## Contract

MCP over SSH stdio. The conductor launches the server on demand:

```
ssh -i ~/.ssh/claudevm_ed25519 claude@<host> \
    ~/fleet-worker-node/.venv/bin/python \
    ~/fleet-worker-node/scripts/worker-mcp-server.py
```

- Tools: `node_status`, `ping` (S1 adds `run_plan`, `agent_status`, `get_progress`)
- Resources: `worker://node` (serves `node.json`)

## Setup on a worker (Linux)

```bash
python3 -m venv ~/fleet-worker-node/.venv
~/fleet-worker-node/.venv/bin/python -m pip install "mcp==1.28.1"
```

## S0 demo (run from the OMEN conductor)

```
python scripts/mcp_call.py \
  --ssh "ssh -i $HOME/.ssh/claudevm_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new claude@172.19.133.70" \
  --python /home/claude/fleet-worker-node/.venv/bin/python \
  --server /home/claude/fleet-worker-node/scripts/worker-mcp-server.py \
  --tool node_status
```

Sign-off: `node_status` returns the VM's state over MCP, OMEN→VM, no SSHFS.
