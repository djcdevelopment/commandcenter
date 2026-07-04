#!/usr/bin/env python3
"""MCP control surface for the AM4 fleet node.

Run through SSH as a stdio MCP server:

  ssh derek@am4.tail8e749c.ts.net /home/derek/am4-fleet-node/.venv/bin/python /home/derek/am4-fleet-node/scripts/am4-mcp-server.py
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from mcp.server.fastmcp import FastMCP


ROOT = Path(os.environ.get("AM4_FLEET_ROOT", "/home/derek/am4-fleet-node"))
CONFIG = Path(os.environ.get("AM4_FLEET_CONFIG", "/home/derek/.config/am4-fleet"))
TOKEN_FILE = CONFIG / "oxen.token"
OXEN_ENV = CONFIG / "oxen.env"

mcp = FastMCP("am4-fleet-node")


def run(args: list[str], timeout: int = 20) -> dict[str, Any]:
    proc = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    return {
        "args": args,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def shell(command: str, timeout: int = 20) -> dict[str, Any]:
    return run(["bash", "-lc", command], timeout=timeout)


def token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def http_json(url: str, authorized: bool = False, timeout: int = 5) -> dict[str, Any]:
    headers = {}
    if authorized and token():
        headers["Authorization"] = f"Bearer {token()}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as response:  # noqa: S310 - local operator URL only
            body = response.read().decode("utf-8")
            payload = json.loads(body)
            if isinstance(payload, dict):
                payload.setdefault("http_status", response.status)
            return payload
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"body": body}
        if isinstance(payload, dict):
            payload["http_status"] = exc.code
        return payload


def render_owner_lines() -> str:
    result = shell(
        "for node in /dev/dri/renderD128 /dev/dri/renderD129; do "
        "echo \"--- $node\"; "
        "[ -e \"$node\" ] && fuser -v \"$node\" 2>&1 || true; "
        "done"
    )
    return result["stdout"] + result["stderr"]


def render_busy() -> bool:
    owners = render_owner_lines()
    return "COMMAND" in owners and any(name in owners for name in ("python", "llama", "ComfyUI"))


@mcp.resource("am4://node")
def node_resource() -> str:
    return (ROOT / "node.json").read_text(encoding="utf-8")


@mcp.resource("am4://oxen/env")
def oxen_env_resource() -> str:
    if not OXEN_ENV.exists():
        return "oxen.env not found"
    lines = []
    for line in OXEN_ENV.read_text(encoding="utf-8").splitlines():
        if "TOKEN=" in line or "PASSWORD=" in line:
            key = line.split("=", 1)[0]
            lines.append(f"{key}=<redacted>")
        else:
            lines.append(line)
    return "\n".join(lines)


@mcp.resource("am4://gpu/render-owners")
def render_owners_resource() -> str:
    return render_owner_lines()


@mcp.tool()
def node_status(include_xpu_smoke: bool = False) -> dict[str, Any]:
    """Return AM4 node status. XPU smoke is opt-in because it touches both cards."""
    args = [str(ROOT / "scripts" / "am4-node-status.sh")]
    if include_xpu_smoke:
        args.append("--xpu-smoke")
    return run(args, timeout=120 if include_xpu_smoke else 30)


@mcp.tool()
def render_owners() -> str:
    """Show processes holding AM4 GPU render nodes."""
    return render_owner_lines()


@mcp.tool()
def oxen_facade_health() -> dict[str, Any]:
    """Return the AM4 oxen OpenAI facade health payload."""
    return http_json("http://127.0.0.1:8090/health", authorized=False, timeout=5)


@mcp.tool()
def oxen_models() -> dict[str, Any]:
    """List oxen OpenAI facade aliases and readiness."""
    return http_json("http://127.0.0.1:8090/v1/models", authorized=True, timeout=5)


@mcp.tool()
def oxen_ready(alias: str = "oxen-planner") -> dict[str, Any]:
    """Run the facade readiness probe for an alias."""
    quoted = quote(alias, safe="")
    return http_json(f"http://127.0.0.1:8090/oxen/ready?alias={quoted}", authorized=True, timeout=35)


@mcp.tool()
def oxen_backend_status() -> dict[str, Any]:
    """Return systemd status for the heavy llama.cpp backend."""
    return run(["systemctl", "is-active", "am4-oxen-backend.service"], timeout=5)


@mcp.tool()
def start_oxen_backend(force: bool = False) -> dict[str, Any]:
    """Start the heavy dual-B70 backend. Refuses while render nodes are busy unless force=true."""
    owners = render_owner_lines()
    if not force and ("COMMAND" in owners and any(name in owners for name in ("python", "llama", "ComfyUI"))):
        return {
            "started": False,
            "refused": True,
            "reason": "render nodes are busy; pass force=true only if co-tenancy is deliberate",
            "render_owners": owners,
        }
    result = run(["sudo", "systemctl", "start", "am4-oxen-backend.service"], timeout=20)
    return {"started": result["returncode"] == 0, "systemctl": result, "render_owners_before": owners}


@mcp.tool()
def stop_oxen_backend() -> dict[str, Any]:
    """Stop the heavy dual-B70 backend."""
    result = run(["sudo", "systemctl", "stop", "am4-oxen-backend.service"], timeout=20)
    return {"stopped": result["returncode"] == 0, "systemctl": result}


@mcp.tool()
def long_context_memory_plan(ctx: int = 131072, kv_type: str = "q8_0") -> dict[str, Any]:
    """Return the current AM4 long-context target and caveats."""
    q8_kv_gib = ctx * 48 * 1024 / 1024**3
    f16_kv_gib = ctx * 96 * 1024 / 1024**3
    return {
        "ctx": ctx,
        "kv_type": kv_type,
        "model": "Qwen3-30B-A3B-Instruct-2507-Q4_K_M",
        "placement": "llama.cpp layer split across both B70s for the default service",
        "estimated_total_kv_gib": {
            "q8": round(q8_kv_gib, 2),
            "f16": round(f16_kv_gib, 2),
            "source": "Denning measured Qwen3 MoE KV at about 96 KiB/token f16, 48 KiB/token q8.",
        },
        "current_policy": {
            "backend": "llama.cpp SYCL / Level Zero",
            "device_list": "0,1",
            "split_mode": "layer",
            "tensor_split": "1,1",
            "parallel_slots": 1,
            "flash_attn": "on",
            "fit": "off",
            "mmap": "enabled by default on Linux",
            "candidate_kv_on_one_card_policy": "split_mode=row with main_gpu set; must be benchmarked before becoming default",
        },
        "host_memory_posture": {
            "ddr_gb": 32,
            "pooled_memory": "not a first-order plan on AM4",
            "target": "steady-state resident model/KV inside B70 VRAM; host memory is control-plane/swap headroom only",
            "custom_tooling_bias": "build probes or lower-level controls where the public stack does not expose placement truth",
        },
        "important_caveat": (
            "AM4 is Linux, so explicit pinned placement should be much easier than the Windows/VidMm "
            "case Denning measured. The remaining question is whether the chosen serving engine exposes "
            "the desired policy cleanly enough: KV on one B70, weights/compute on another, or dual-layer split."
        ),
        "denning_bounds": denning_bounds(),
    }


@mcp.tool()
def denning_bounds() -> dict[str, Any]:
    """Return measured Denning bounds and mark which ones must be re-tested on Ubuntu."""
    return {
        "rig": "dual Intel Arc Pro B70, Qwen3-30B-A3B-Instruct-2507-Q4_K_M",
        "scope": {
            "source_os": "Windows/VidMm",
            "am4_os": "Ubuntu/Linux",
            "use_as": "bounds ledger and cautionary data, not the Linux control law",
        },
        "kv_size": {
            "f16": "about 96 KiB/token",
            "q8": "about 48 KiB/token",
            "q8_quality": "wikitext-2 PPL statistically identical to f16 in the Denning battery",
        },
        "single_stream_capacity": {
            "f16_128k": "fit in dedicated VRAM with no observed spill in the Windows battery",
            "q8_128k": "fit; half KV bytes, about 10 percent decode penalty",
            "q8_256k": "plausible capacity-wise, but must be measured on Ubuntu before treating it as serviceable",
        },
        "performance_bounds": {
            "decode_cliff": "f16 0->64k dropped 22x; q8 0->128k dropped 41x",
            "q8_128k_decode": "about 2.5 tok/s single-card in the Windows Vulkan battery",
            "flash_attn": "Vulkan flash-attn is required at depth but costs about 3x decode at 16k; FA-off wedged at 64k",
        },
        "residency_bounds": {
            "restore_vs_reprefill": "114x to 219x faster restore; 64k cold re-prefill about 9.3 min vs restore under 4s",
            "windows_host_constraint": "host commit/swap tier was the Windows system wall",
            "linux_retest": "verify pinned allocation and swap behavior on Ubuntu before carrying over any host-commit control law",
            "admission": "Windows Denning capped by live VidMm budget plus compute knee; Linux should use explicit placement/pinning plus measured engine behavior",
        },
        "p2p_status": {
            "windows_measurement": "card-to-card path measured about 6.48 GB/s, approximately 0.47x PCIe, consistent with host bounce",
            "ubuntu_expectation": "Linux/Level Zero should make pinned placement and possibly direct device paths easier",
            "ubuntu_status": "not yet measured in this package; run a Level Zero/SYCL throughput probe before relying on KV/model separation",
        },
        "sycl_multicard_status": {
            "windows_10": "failed for multi-card, motivating the prior Vulkan workaround",
            "ubuntu_am4": "target path; must be proven before row/tensor placement experiments",
        },
    }


@mcp.tool()
def llama_cpp_placement_modes() -> dict[str, Any]:
    """Summarize llama.cpp placement modes relevant to the oxen facade on dual B70."""
    return {
        "backend": "SYCL / Level Zero preferred on Ubuntu AM4; Vulkan remains a fallback",
        "baseline": {
            "args": "-dev 0,1 -sm layer -ts 1,1 -c 131072 -ctk q8_0 -ctv q8_0 -np 1",
            "meaning": "split layers and KV across both GPUs; lowest-risk first resident service",
        },
        "kv_on_one_card_candidate": {
            "args": "-dev 0,1 -sm row -ts 1,1 -mg 0 or -mg 1",
            "meaning": "row-split weights across GPUs; main GPU owns intermediate results and KV per llama.cpp help text",
            "status": "needs AM4 benchmark for throughput, VRAM placement, and stability",
        },
        "tensor_candidate": {
            "args": "-dev 0,1 -sm tensor -ts 1,1",
            "meaning": "parallelized split of weights and KV; may be useful if SYCL kernels handle it well",
            "status": "needs AM4 benchmark",
        },
        "measurement_order": [
            "run a full context ladder, not a single 16k smoke",
            "compare single-card and layer split across 0, 8k, 16k, 32k, 64k, and 131k",
            "only test row/tensor in bounded ladders because row has already segfaulted at 16k",
            "only then try 262144 context",
        ],
        "acceptance": [
            "both cards used as intended",
            "no steady-state host spill",
            "oxen alias readiness succeeds through /v1",
            "throughput is usable at the target context, not merely at small context",
        ],
    }


@mcp.tool()
def am4_operating_posture() -> dict[str, Any]:
    """Return the current engineering posture for AM4."""
    return {
        "northbound_control": "MCP over SSH stdio",
        "model_endpoint": "OpenAI-compatible oxen facade on :8090",
        "event_bus": "NATS optional, not required for first slice",
        "memory_strategy": {
            "not_chasing": "pooled/shared memory as VRAM extension on 32 GB DDR",
            "chasing": "explicit pinned per-device placement, no host spill, one reliable long-context backend",
            "first_context_target": 131072,
            "later_context_target": "262144 only after placement probes pass",
        },
        "tooling_strategy": [
            "prefer Linux SYCL/Level Zero path",
            "measure before assuming P2P or placement semantics",
            "build local probes/shims where llama.cpp/runtime telemetry is insufficient",
            "consider kernel/driver-level work only after user-space probes show the missing control",
        ],
    }


@mcp.tool()
def accelerator_capabilities() -> dict[str, Any]:
    """Return non-invasive accelerator capability probes available on AM4."""
    sycl = shell("source /opt/intel/oneapi/setvars.sh >/tmp/oneapi-setvars.log 2>&1 || true; sycl-ls 2>&1", timeout=10)
    cl = shell("clinfo 2>/dev/null | grep -E 'Platform Name|Device Name|unified_shared_memory|external_memory|khr_pci_bus_info' | sed -n '1,120p'", timeout=10)
    vk = shell("vulkaninfo --summary 2>/dev/null | sed -n '1,120p'", timeout=10)
    return {
        "sycl_ls": sycl,
        "opencl_relevant_extensions": cl,
        "vulkan_summary": vk,
        "interpretation": (
            "These probes show Linux exposes both B70s through Level Zero/OpenCL/Vulkan. They do not "
            "prove the serving engine can express the desired pinned KV/model placement policy."
        ),
    }


@mcp.prompt()
def safe_oxen_long_context_run() -> str:
    """Operational prompt for agents before starting an oxen long-context run on AM4."""
    return (
        "Before starting AM4 oxen long-context inference: call render_owners; refuse to start "
        "the backend if ComfyUI or another process owns both render nodes unless the operator "
        "explicitly asks for co-tenancy. Start with the configured 131072 context, q8_0 KV, "
        "one server slot, and a single alias readiness probe. Treat Denning as Windows bounds; "
        "on Ubuntu prefer explicit pinned placement, then measure whether the engine actually "
        "supports the KV/model split you intend."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
