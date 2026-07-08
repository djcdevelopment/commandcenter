"""HEARTH tool provider: summon (Stream H-B) — wake_am4 LIVE; the rest stubs.

The lab summons capacity through its own tools (HEARTH L2: AM4/VMs are capacity, not
topology). wake_am4 is live (H3); start_ollama / checkpoint_vm still return
{ok: false, stub: true, would_run: <the real command>} so callers and the ledger see
the correct tool shapes now.

Command provenance:
- wake_am4 / oxen backend (LIVE, ground-truthed on AM4 2026-07-07): AM4 is native
  Ubuntu. The always-on facade am4-oxen-facade.service (:8090,
  am4-fleet-node/scripts/oxen-facade.py) proxies a llama.cpp SYCL backend on
  127.0.0.1:8080; its /health reports backend.ok — the serve-truth this tool keys on.
  The managed backend is the systemd --user slot unit b70-planner.service
  (Qwen3-30B-A3B, single-card SYCL0; enabled + lingered — see
  am4-fleet-node/B70-CARD-MANAGEMENT.md), so waking = `systemctl --user start
  b70-planner`. Deliberately NOT `nohup ~/baseline/relaunch-qwen3-baseline.sh &`
  over SSH: runbook gotcha #2 (SSH-detach swallow) rules nohup out, and the relaunch
  script's dual-card split (-dev SYCL0,SYCL1, 131k ctx) is an experiment throughput
  mode that grabs BOTH cards — it would stomp the critic slot (:8081) and imagegen;
  its ad-hoc lane is `systemd-run --user` per the runbook.
  Why no dedicated banked-fire unit for the dual-card baseline: :8080 already has a
  systemd claimant (b70-planner); a second latent claimant invites unit-vs-unit
  races — the zombie am4-planner/am4-critic units crash-looped 58,922 times against
  a deleted start-hermes-backend.sh before being disabled (2026-07-07). Fewer latent
  launchers on a shared-GPU box is the lesson.
- start_ollama: omen-worker-1 runs Ollama (login-start today; service-ification is the
  Δ4 decision). `ollama serve` starts the daemon; hitting /api/generate with the model
  loads it resident.
- checkpoint_vm: Hyper-V PowerShell on the OMEN host — the proven VM provisioning path
  (Checkpoint-VM / Export-VMSnapshot / Import-VM playbook; differencing-off-live-VHD
  is retired because claudefarm1 runs permanently).
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Callable, Optional

# occupancy is the one shared SSH/probe discipline for AM4 (BatchMode, timeouts,
# render-node serve-truth); summon reuses it rather than growing a second one.
from hearth.toolsurface import occupancy as _occupancy

AM4_SSH = "ssh derek@am4.tail8e749c.ts.net"
AM4_OXEN_HEALTH = "http://100.116.82.60:8090/health"   # facade IP = backends.toml endpoint
AM4_PLANNER_UNIT = "b70-planner.service"
WAKE_AM4_CMD = f"systemctl --user start {AM4_PLANNER_UNIT}"
WAKE_POLL_INTERVAL_S = 5.0
OLLAMA_DEFAULT_ENDPOINT = "http://127.0.0.1:11434"

# Render-node holders that suggest imagegen (ComfyUI shows up as python). NB:
# ComfyUI holds BOTH render nodes even when idle (runbook: "it holds renderD128/
# renderD129 even when idle"), so holder-presence alone cannot refuse a wake —
# _imagegen_active supplies the in-flight truth. A holder matching only "llama" is
# one of our own slot units (the critic resident on SYCL1, or the planner itself
# mid-load) — starting the managed planner unit alongside those is the designed
# layout and idempotent, so it never refuses.
_IMAGEGEN_MARKERS = ("python", "ComfyUI")
_COMFYUI_QUEUE_CMD = "curl -s -m 3 http://127.0.0.1:8188/queue"

_sleep = time.sleep  # module seam so tests don't wait out real poll intervals


def _fetch_health(timeout_s: float = 5.0) -> dict:
    """GET the oxen facade /health. backend.ok is the serve-truth for :8080."""
    try:
        with urllib.request.urlopen(AM4_OXEN_HEALTH, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - reported to the caller, never raised
        return {"reachable": False, "backend_ok": False,
                "error": f"{type(exc).__name__}: {exc}"}
    backend = payload.get("backend") or {}
    return {"reachable": True, "backend_ok": bool(backend.get("ok")), "backend": backend}


def _check_occupancy() -> dict:
    return _occupancy.check_occupancy("am4-oxen")


def _ssh(command: str) -> tuple[Optional[str], Optional[str]]:
    return _occupancy._run_ssh(command, _occupancy.SSH_TIMEOUT_S)


def _imagegen_active() -> dict:
    """Is ComfyUI actually rendering on the B70s? ComfyUI holds both render nodes
    24/7, so holder-presence can't refuse a wake — the :8188/queue is the in-flight
    truth (queue_running or queue_pending non-empty).

    Returns {"active": True|False|None, "detail": str}; active is None when the
    queue is unreachable or unparseable (unverifiable — the caller refuses to be
    safe unless force=True)."""
    output, error = _ssh(_COMFYUI_QUEUE_CMD)
    if error is not None or not (output or "").strip():
        return {"active": None, "detail": error or "empty response from :8188/queue"}
    try:
        queue = json.loads(output.strip())
    except ValueError:
        return {"active": None, "detail": "unparseable :8188/queue response"}
    if not isinstance(queue, dict):
        return {"active": None, "detail": "unexpected :8188/queue payload"}
    running = queue.get("queue_running") or []
    pending = queue.get("queue_pending") or []
    return {"active": bool(running or pending),
            "detail": f"queue_running={len(running)} queue_pending={len(pending)}"}


def wake_am4(force: bool = False, wait_s: int = 120) -> dict:
    """Wake AM4's oxen inference backend (idempotent, occupancy-gated).

    Serve-truth first: if the always-on facade (:8090) already reports backend.ok
    for :8080, this is a no-op. Otherwise the B70 render nodes are checked for
    imagegen ownership; an in-flight (or unverifiable) ComfyUI job refuses the wake
    unless force=True — idle ComfyUI merely holding the nodes does not block. Then
    the managed planner slot is started over SSH (systemctl --user start
    b70-planner) and the facade health is polled until the backend answers or
    wait_s elapses (wait_s=0 = fire-and-forget).
    """
    if wait_s < 0:
        raise ValueError("wait_s must be >= 0")

    health = _fetch_health()
    if health["backend_ok"]:
        return {"ok": True, "node": "am4", "action": "already-serving",
                "health": health["backend"]}

    occupation = _check_occupancy()
    detail = str(occupation.get("detail", ""))
    imagegen_holds_cards = (occupation.get("occupancy") == "busy"
                            and any(marker in detail for marker in _IMAGEGEN_MARKERS))
    if imagegen_holds_cards and not force:
        # Idle ComfyUI holds the render nodes 24/7; only an in-flight (or
        # unverifiable) imagegen job refuses the wake.
        queue = _imagegen_active()
        if queue["active"] is not False:
            return {"ok": False, "node": "am4", "action": "refused",
                    "reason": ("imagegen holds the B70 render nodes and the ComfyUI "
                               "queue is " + ("busy" if queue["active"] else "unverifiable")
                               + "; pass force=True to wake the planner slot anyway"),
                    "comfyui_queue": queue, "occupancy": occupation}
    # busy-with-only-llama holders are our own slot units, and "unknown" proceeds
    # too: a summon is a deliberate, pinned-style action (occupancy.resolve_for_lane).

    _, error = _ssh(WAKE_AM4_CMD)
    if error is not None:
        return {"ok": False, "node": "am4", "action": "ssh-failed", "error": error,
                "would_run": f"{AM4_SSH} '{WAKE_AM4_CMD}'"}

    attempts = int(wait_s // WAKE_POLL_INTERVAL_S)
    for _ in range(attempts):
        _sleep(WAKE_POLL_INTERVAL_S)
        health = _fetch_health()
        if health["backend_ok"]:
            return {"ok": True, "node": "am4", "action": "woken",
                    "unit": AM4_PLANNER_UNIT, "health": health["backend"]}
    return {"ok": False, "node": "am4", "action": "started-not-ready",
            "unit": AM4_PLANNER_UNIT,
            "note": (f"unit start dispatched but the facade did not report backend.ok "
                     f"within {wait_s}s; inspect: "
                     f"{AM4_SSH} 'journalctl --user -u {AM4_PLANNER_UNIT} -n 50'"),
            "health": health}


def _stub(would_run: str, **extra: object) -> dict:
    return {"ok": False, "stub": True, "would_run": would_run, **extra}


def start_ollama(model: str = "qwen3-coder:30b") -> dict:
    """[stub] Start the local Ollama daemon and load a model resident."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    return _stub(
        "ollama serve",
        then=(
            f"curl -s {OLLAMA_DEFAULT_ENDPOINT}/api/generate "
            f"-d '{{\"model\": \"{model}\", \"prompt\": \"warmup\", \"stream\": false}}'"
        ),
        model=model,
        node="omen-worker-1",
        note="login-start today; always-on service-ification is the pending Δ4 decision",
    )


def checkpoint_vm(name: str) -> dict:
    """[stub] Checkpoint a Hyper-V builder VM on the OMEN host (the proven snapshot path)."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty VM name")
    return _stub(
        f"powershell.exe -NoProfile -Command \"Checkpoint-VM -Name '{name}' "
        f"-SnapshotName 'hearth-checkpoint'\"",
        export=(
            f"powershell.exe -NoProfile -Command \"Export-VMSnapshot -VMName '{name}' "
            f"-Name 'hearth-checkpoint' -Path 'C:\\vm-exports'\""
        ),
        vm=name,
        host="omen (Hyper-V host)",
    )


def get_tools() -> list[Callable]:
    return [wake_am4, start_ollama, checkpoint_vm]
