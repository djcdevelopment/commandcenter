"""HEARTH tool provider: summon (Stream H-B) — STUBS with real shapes.

The lab summons capacity through its own tools (HEARTH L2: AM4/VMs are capacity, not
topology). These stubs return {ok: false, stub: true, would_run: <the real command>}
so callers and the ledger see the correct tool shapes NOW; H3 flips them live.

Command provenance:
- wake_am4 / oxen backend: am4-fleet-node/node.json (ssh endpoint). The live AM4
  inference muscle is two systemd --user slot units — b70-planner (B70 SYCL0 :8080,
  Qwen3-30B) + b70-critic (B70 SYCL1 :8081, qwen2.5-14b) — fronted by the
  am4-oxen-facade.service (:8090). They are enabled+lingered (survive reboot); see
  am4-fleet-node/B70-CARD-MANAGEMENT.md. Check render-node ownership first — B70s may
  be owned by image gen.
- start_ollama: omen-worker-1 runs Ollama (login-start today; service-ification is the
  Δ4 decision). `ollama serve` starts the daemon; hitting /api/generate with the model
  loads it resident.
- checkpoint_vm: Hyper-V PowerShell on the OMEN host — the proven VM provisioning path
  (Checkpoint-VM / Export-VMSnapshot / Import-VM playbook; differencing-off-live-VHD
  is retired because claudefarm1 runs permanently).
"""

from __future__ import annotations

from typing import Callable

AM4_SSH = "ssh derek@am4.tail8e749c.ts.net"
AM4_OXEN_HEALTH = "http://am4.tail8e749c.ts.net:8090/health"
OLLAMA_DEFAULT_ENDPOINT = "http://127.0.0.1:11434"


def _stub(would_run: str, **extra: object) -> dict:
    return {"ok": False, "stub": True, "would_run": would_run, **extra}


def wake_am4() -> dict:
    """[stub] Wake AM4's inference muscle: start the dual-B70 oxen backend over SSH."""
    return _stub(
        f"{AM4_SSH} 'systemctl --user start b70-planner b70-critic'",
        preflight=(
            f"{AM4_SSH} 'curl -s localhost:8188/queue' — the B70s are shared with imagegen "
            "(ComfyUI); confirm queue_running/queue_pending are empty (or accept single-card "
            "coexistence) before loading both slots. Facade (:8090) stays up regardless."
        ),
        verify=f"curl -s {AM4_OXEN_HEALTH}",
        node="am4",
    )


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
