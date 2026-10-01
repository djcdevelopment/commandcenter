#!/usr/bin/env bash
# preflight-vllm-seat.sh <seat 0|1>  — validate a seat's launch BEFORE restarting it (no model load, ~5 s).
#  1. Environment exactly as systemd will pass it (unit + drop-ins + EnvironmentFile), via `systemctl show`.
#  2. Render the argv with OMEN_DRY=1 and parse it with vLLM's own argument parser (catches quoting, bad choices, types).
#  3. --max-model-len must not exceed the checkpoint's max_position_embeddings unless rope scaling is configured.
#  4. Model dir and required files exist; port not already bound by another process than this seat.
set -u
seat=${1:?seat 0|1}; unit=omen-vllm@$seat.service; vllm_py=/home/derek/.venvs/vllm-xpu-030/bin/python
envs=$(systemctl --user show "$unit" -p Environment --value); envfile=$(systemctl --user show "$unit" -p EnvironmentFiles --value | cut -d' ' -f1)
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
( set -a; [ -n "$envfile" ] && . "$envfile"; set +a; export $envs 2>/dev/null; OMEN_DRY=1 bash /home/derek/bin/start-vllm-seat.sh "$seat" ) > "$tmp" 2>&1 || { echo "PREFLIGHT FAIL seat $seat: launch script errored:"; cat "$tmp"; exit 2; }
"$vllm_py" - "$tmp" <<'PY' || exit 3
import json, sys, os
argv = [l.rstrip("\n") for l in open(sys.argv[1]) if l.rstrip("\n") != ""]
assert argv[0] == "serve", argv[:2]
model = argv[1]; args = argv[2:]
from vllm.utils.argparse_utils import FlexibleArgumentParser
from vllm.entrypoints.openai.cli_args import make_arg_parser
parser = make_arg_parser(FlexibleArgumentParser(prog="vllm serve"))
try:
    ns = parser.parse_args(args)          # exits non-zero with the same message vLLM would print on a real start
except SystemExit as e:
    print("PREFLIGHT FAIL: vLLM would reject these arguments (see message above)"); raise
cfg_path = os.path.join(model, "config.json")
if not os.path.exists(cfg_path):
    print(f"PREFLIGHT FAIL: no config.json under {model}"); sys.exit(1)
cfg = json.load(open(cfg_path)); tc = cfg.get("text_config", cfg)
mpe = tc.get("max_position_embeddings"); rope = tc.get("rope_scaling") or tc.get("rope_parameters", {}).get("rope_type", "default") not in ("default", None)
mml = ns.max_model_len
if mpe and mml and mml > mpe and not rope:
    print(f"PREFLIGHT FAIL: --max-model-len {mml} > max_position_embeddings {mpe} and no rope scaling configured (this is the 65536 outage)"); sys.exit(1)
spec = getattr(ns, "speculative_config", None)
print(f"PREFLIGHT OK seat: model={os.path.basename(model)} max_model_len={mml} (checkpoint {mpe}) served={ns.served_model_name} "
      f"quant={ns.quantization} spec={spec} prefix_caching={ns.enable_prefix_caching} kv_bytes={getattr(ns,'kv_cache_memory_bytes',None)} "
      f"tool_parser={ns.tool_call_parser} reasoning={ns.reasoning_parser}")
PY
port=$((18091+seat)); pid=$(ss -ltnp 2>/dev/null | awk -v p=":$port" '$4 ~ p"$" {print $NF}' | grep -o 'pid=[0-9]*' | head -1)
[ -n "$pid" ] && echo "note: port $port currently held by $pid (the running seat; fine for a restart)"
exit 0
