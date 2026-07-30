"""HEARTH tool provider: summon (Stream H-B) — wake_am4 LIVE; the rest stubs.

The lab summons capacity through its own tools (HEARTH L2: AM4/VMs are capacity, not
topology). wake_am4 is live (H3); start_ollama / checkpoint_vm still return
{ok: false, stub: true, would_run: <the real command>} so callers and the ledger see
the correct tool shapes now.

Command provenance:
- wake_am4 / resident moe (retargeted 2026-07-30, ADR-0029): planner/critic are CUT
  from service — gpt-oss-120b (b70-moe.service, dual-card, :8082) serves every AM4
  LLM role, so waking AM4 means `systemctl --user start b70-moe`. Serve-truth is
  llama-server's own /health on :8082 (auth-exempt; 503 while loading, 200 when the
  model is resident — the port binds BEFORE the load, so port-open is NOT readiness;
  see B70-VERTICAL-TRACE.html for the incident that taught this). The unit carries
  Conflicts=b70-planner/b70-critic (hardened 309b7fc), so a wake also enforces the
  single-tenant topology. A cold load off the ntfs3 mount takes ~3–5 min — hence the
  generous default wait. Historical: the pre-0029 wake started b70-planner via the
  :8090 facade's backend.ok (ground-truthed 2026-07-07); that lane's disposition is
  an ADR-0029 follow-up. Still deliberately NOT `nohup … &` over SSH: runbook
  gotcha #2 (SSH-detach swallow), and fewer latent launchers on a shared-GPU box is
  the standing lesson (zombie am4-planner/critic units crash-looped 58,922× in the
  hermes era; the 2026-07-30 standoff was the same class).
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
import urllib.error
import urllib.request
from typing import Callable, Optional

# occupancy is the one shared SSH/probe discipline for AM4 (BatchMode, timeouts,
# render-node serve-truth); summon reuses it rather than growing a second one.
from hearth.toolsurface import occupancy as _occupancy

AM4_SSH = "ssh derek@192.168.12.233"                    # LAN, not tailnet (ADR-0014)
AM4_MOE_HEALTH = "http://192.168.12.233:8082/health"    # llama-server serve-truth (auth-exempt)
AM4_WAKE_UNIT = "b70-moe.service"                       # the sole LLM tenant (ADR-0029)
WAKE_AM4_CMD = f"systemctl --user start {AM4_WAKE_UNIT}"
WAKE_POLL_INTERVAL_S = 5.0
OLLAMA_DEFAULT_ENDPOINT = "http://127.0.0.1:11434"

# Render-node holders that suggest imagegen (ComfyUI shows up as python). NB:
# ComfyUI holds BOTH render nodes even when idle (runbook: "it holds renderD128/
# renderD129 even when idle"), so holder-presence alone cannot refuse a wake —
# _imagegen_active supplies the in-flight truth. A holder matching only "llama" is
# our own moe mid-load (or a deliberately revived planner/critic — in which case
# starting b70-moe swaps tenancy via Conflicts=, which is the wake's whole point),
# so llama-only holders never refuse.
_IMAGEGEN_MARKERS = ("python", "ComfyUI")
_COMFYUI_QUEUE_CMD = "curl -s -m 3 http://127.0.0.1:8188/queue"

_sleep = time.sleep  # module seam so tests don't wait out real poll intervals


def _fetch_health(timeout_s: float = 5.0) -> dict:
    """GET the moe llama-server /health on :8082. HTTP 200 = model resident (the
    server 503s while loading — port-open alone is never readiness)."""
    try:
        with urllib.request.urlopen(AM4_MOE_HEALTH, timeout=timeout_s) as response:
            code = response.status
            try:
                body = json.loads(response.read().decode("utf-8"))
            except ValueError:
                body = {}
    except urllib.error.HTTPError as exc:  # 503 "Loading model" lands here
        return {"reachable": True, "backend_ok": False,
                "backend": {"ok": False, "status": exc.code}}
    except Exception as exc:  # noqa: BLE001 - reported to the caller, never raised
        return {"reachable": False, "backend_ok": False,
                "error": f"{type(exc).__name__}: {exc}"}
    ok = code == 200
    return {"reachable": True, "backend_ok": ok,
            "backend": {"ok": ok, "status": code, **({"body": body} if body else {})}}


def _check_occupancy() -> dict:
    # Render-node serve-truth, probed DIRECTLY (not via the _PROBES registry, whose
    # "am4-oxen" entry is being repointed post-ADR-0029): the wake gate's question
    # is exactly "who holds the cards", which is what fuser answers.
    return _occupancy.probe_render_owners()


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


def wake_am4(force: bool = False, wait_s: int = 360) -> dict:
    """Wake AM4's resident moe (gpt-oss-120b, ADR-0029 — idempotent, occupancy-gated).

    Serve-truth first: if llama-server's /health on :8082 already answers 200, this
    is a no-op (the port binds before the load, so only /health 200 is readiness).
    Otherwise the B70 render nodes are checked for imagegen ownership; an in-flight
    (or unverifiable) ComfyUI job refuses the wake unless force=True — idle ComfyUI
    merely holding the nodes does not block. Then b70-moe is started over SSH
    (Conflicts= swaps out any revived planner/critic automatically) and /health is
    polled until 200 or wait_s elapses (wait_s=0 = fire-and-forget). A cold load
    off the ntfs3 mount takes ~3-5 min — the default wait covers it.
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
                               + "; pass force=True to wake the moe anyway"),
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
                    "unit": AM4_WAKE_UNIT, "health": health["backend"]}
    return {"ok": False, "node": "am4", "action": "started-not-ready",
            "unit": AM4_WAKE_UNIT,
            "note": (f"unit start dispatched but /health did not reach 200 within "
                     f"{wait_s}s (a cold 120b load takes ~3-5 min); inspect: "
                     f"{AM4_SSH} 'journalctl --user -u {AM4_WAKE_UNIT} -n 50'"),
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
