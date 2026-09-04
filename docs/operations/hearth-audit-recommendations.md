# HEARTH Network & Control Plane Audit: Findings, Reasoning, and Recommendations

**Date:** 2026-09-04  
**Context:** Comprehensive audit of `hearth/` following recent network migrations, hardware swaps (OMEN motherboard replacement, Intel Arc Pro B70 GPU relocation to OMEN, AM4 Wi-Fi transition, FX99 CUDA sidecar enlistment, and Hyper-V Default Switch subnet dynamics).

---

## 1. Network Ground Truth Matrix

The table below contrasts the **declared architecture** against **measured live reality** across all physical and virtual nodes.

| Node | Interface / Transport | Expected Address | Measured IP / Name | Service / Port | Live State | Finding / Discrepancy |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **OMEN** (Host) | Ethernet 3 (10GbE) | `192.168.12.239` | `192.168.12.239` | LAN Ingress | **UP** | Post-motherboard swap address is stable; old `.194` Wi-Fi is defunct. |
| **OMEN** (Host) | Loopback | `127.0.0.1` | `127.0.0.1` | `:8710` (Hearth) | **UP** | FastMCP server active (22/22 providers loaded). |
| **OMEN** (Host) | Loopback | `127.0.0.1` | `127.0.0.1` | `:8711` (Caddy) | **UP** | Reverse proxy active; strip/filter headers working. |
| **OMEN** (Host) | Loopback | `127.0.0.1` | `127.0.0.1` | `:8081` (llama-swap) | **UP** | v251 active; managing B70 model lifecycle. |
| **OMEN** (Host) | Loopback | `127.0.0.1` | `127.0.0.1` | `:8082` (`omen-arc`) | **UP** | `qwen3-30b-a3b` dual B70 decoding at ~107.8 tok/s (`at_rate`). |
| **OMEN** (Host) | All Interfaces | `0.0.0.0` | `0.0.0.0` | `:11435` (Tracing Proxy) | **UP** (Ghost) | Proxy socket answers, but backing Ollama on `:11434` is retired (ADR-0034). |
| **OMEN** (Host) | Tailscale | `100.124.12.37` | `omen.tail8e749c.ts.net` | `:443` (Funnel) | **UP** | Targets Caddy `:8711`. |
| **OMEN** (Host) | Tailscale | `100.124.12.37` | `omen.tail8e749c.ts.net` | `:8443` (Serve) | **UP** | Private tailnet-only path to `:8710`; actively serving `botherder-am4`. |
| **AM4** | Wi-Fi (`wlp4s0`) | `192.168.12.233` | `192.168.12.233` | `:22` (SSH) | **UP** | Accepts user `derek@` only; `claude@` key was not reinstalled. |
| **AM4** | Wi-Fi (`wlp4s0`) | `192.168.12.233` | `192.168.12.233` | `:16686` (Jaeger) | **UP** | Observability traces accessible from OMEN dashboard. |
| **AM4** | Wi-Fi (`wlp4s0`) | `192.168.12.233` | `192.168.12.233` | `:4318` (OTel HTTP) | **UP** | Telemetry ingestion sink active. |
| **AM4** | Wi-Fi (`wlp4s0`) | `192.168.12.233` | `192.168.12.233` | `:8787` (BF6 API) | **UP** | Worker review/status endpoint answering HTTP 200. |
| **AM4** | Wi-Fi (`wlp4s0`) | `192.168.12.233` | `192.168.12.233` | `:8090` (Oxen Facade) | **UP** (Empty) | Facade process lives, but models report `ready: false` (B70s moved). |
| **AM4** | Wi-Fi (`wlp4s0`) | `192.168.12.233` | `192.168.12.233` | `:445` (Samba) | **DOWN** | Connection refused / dropped. Mapped drive `A:` on OMEN is in `Reconnecting` state. |
| **FX99** | Wired (`enp9s0`) | `192.168.12.220` | `192.168.12.220` | `:22` (SSH) | **UP** | Shell accessible for `derek@`. |
| **FX99** | Wired (`enp9s0`) | `192.168.12.220` | `192.168.12.220` | `:11434` (Ollama) | **UP** | 12 resident models serving as sidecar rung (`fx99-ollama`). |
| **FX99** | Wired (`enp9s0`) | `192.168.12.220` | `192.168.12.220` | `:445` (Samba) | **DOWN** | Connection refused. Declared read-only SSD shares (`W:`, `S:`, `Y:`) offline. |
| **cc-conductor** | Hyper-V Default Switch | `cc-conductor.mshome.net` | `172.17.192.0/20` | `:22` (SSH), `:8080` | **UP** | Conductor daemon and dashboard up and responding. |
| **cc-builder-1** | Hyper-V Default Switch | `cc-builder-1.mshome.net` | `172.17.192.0/20` | `:22` (SSH) | **UP** (Scoped) | Reached from `cc-conductor`; OMEN key rejected. Frontier claude runner. |
| **cc-builder-2** | Hyper-V Default Switch | `cc-builder-2.mshome.net` | `172.17.192.0/20` | `:22` (SSH) | **UP** (Scoped) | Successfully points to `http://192.168.12.220:11434/v1` (FX99). |
| **cc-builder-3** | Hyper-V Default Switch | `cc-builder-3.mshome.net` | `172.17.192.0/20` | `:22` (SSH) | **UP** (Scoped) | Successfully points to `http://192.168.12.220:11434/v1` (FX99). |
| **claudefarm1** | Hyper-V Default Switch | `claudefarm1.mshome.net` | `172.17.192.0/20` | `:22` (SSH) | **Degraded** | Accepts SSH from OMEN, but rejects SSH from `cc-conductor`. |

---

## 2. Findings & Architectural Reasoning

### Finding 1: `doorcheck.py` Boolean Evaluation Defect (False "Cold" Alarm)
* **Evidence:** Running `python -m hearth.callers.doorcheck --json` reports `"default_backend_up": false` and `"facets.backend_dependency": "cold"`, despite `omen-arc` being awake and serving inference at 107 tok/s.
* **Root Cause:** In [`doorcheck.py`](file:///c:/work/commandcenter/hearth/callers/doorcheck.py#L337), for all OpenAI-compatible API backends, `entry["up"]` is set to `None` (annotated as purely informational for historical banked-fire backends). Later, at line 382, default backend health is checked with `default_up = bool(entry.get("up"))`. Since `bool(None)` is `False`, making `omen-arc` (`api = "openai"`) the default backend permanently broke the doorcheck's health evaluation.
* **Impact:** Automated monitors (and operators) are conditioned to ignore a permanent red/cold warning, obscuring genuine backend crashes.

---

### Finding 2: Phantom Limbs from B70 Hardware Relocation
* **Evidence:** In [`hearth/toolsurface/`](file:///c:/work/commandcenter/hearth/toolsurface):
  1. [`summon.py:wake_am4`](file:///c:/work/commandcenter/hearth/toolsurface/summon.py#L43-L58) attempts an HTTP GET to `http://192.168.12.233:8082/health`, SSHes to AM4 to start `b70-moe.service`, and probes ComfyUI at `http://127.0.0.1:8188/queue`.
  2. [`dream.py`](file:///c:/work/commandcenter/hearth/toolsurface/dream.py#L27-L48) SSHes to `derek@192.168.12.233` to drive ComfyUI SD3.5-Large.
  3. [`am4.py`](file:///c:/work/commandcenter/hearth/toolsurface/am4.py#L48-L100) probes `http://192.168.12.233:8082/v1/models` and reads `/mnt/win/work/vllama/config/models.json`.
  4. [`occupancy.py`](file:///c:/work/commandcenter/hearth/toolsurface/occupancy.py#L116) retains `MOE_SLOTS_URL = "http://192.168.12.233:8082/slots"`.
* **Root Cause:** The physical Intel Arc Pro B70 cards were relocated to OMEN on 2026-08-20 (ADR-0034), and image generation transitioned to OMEN under [`hearth/imagegen/`](file:///c:/work/commandcenter/hearth/imagegen) (`omen-b70-pool`). While [`hearth/etc/backends.toml`](file:///c:/work/commandcenter/hearth/etc/backends.toml) correctly marked `am4-moe` as `retired = true`, the auxiliary tool surface was never refactored to align with the move.
* **Impact:** Invoking `wake_am4()`, `dream()`, or AM4 catalog tools will stall, hang on SSH timeouts, or return misleading failure states.

---

### Finding 3: Default Switch Subnet Dynamics & Conductor SSH Host Keys
* **Evidence:** Testing SSH from `cc-conductor` to `cc-builder-1`, `cc-builder-2`, and `cc-builder-3` initially aborted with `Host key verification failed`. Adding `-o StrictHostKeyChecking=accept-new` resolved it.
* **Root Cause:** The Hyper-V Default Switch regenerates NAT prefixes across hypervisor reboots (`172.19.240.x` $\to$ `172.29.64.x` $\to$ `172.17.192.x`). Even though sibling DNS (`*.mshome.net`) updates, SSH host keys in `known_hosts` are bound to specific IP/key combinations. If a VM's IP shifts or keys are regenerated, non-interactive BatchMode SSH fails hard.
* **Impact:** Automated build fan-out dispatched by `cc-conductor` can silently fail if a VM's host key isn't automatically accepted.

---

### Finding 4: Storage Lane Outages (Drives `A:`, `W:`, `S:`, `Y:`)
* **Evidence:** 
  1. `net use` reports `Reconnecting A: \\192.168.12.233\AM4`. Direct TCP probe to `192.168.12.233:445` fails.
  2. `fleet-ping` reports `FAIL 192.168.12.220:445 samba ConnectionRefusedError`.
* **Root Cause:**
  1. On AM4, switching from wired to wireless altered interface bindings, or `ufw` lacks an allow rule for port 445 on the wireless interface.
  2. On FX99, `smbd` is either stopped or restricted from binding to `192.168.12.220`.
* **Impact:** Any file operations or tools expecting shared storage across machines encounter extended network timeout stalls (e.g., PowerShell `Get-PSDrive` hung waiting on SMB response).

---

### Finding 5: Environment Boundary Gap & Silent Cloud Escalation
* **Evidence:** Invoking `local_generate()` in an interactive Python shell without sourcing [`hearth/var/gateway.cmd`](file:///c:/work/commandcenter/hearth/var/gateway.cmd) returned:
  `routed_by: "escalation:omen-arc->gcp-gemini"`
* **Root Cause:** [`backends.toml`](file:///c:/work/commandcenter/hearth/etc/backends.toml#L68) marks `omen-arc` with `auth_env = "OMEN_ARC_TOKEN"`. The background Scheduled Task `HearthGatewayBoot` loads `hearth\var\gateway.cmd`, but developer shells and ad-hoc scripts lack this variable. Rather than throwing an authentication error, Hearth's fallback policy treats an unauthenticated local backend as an infrastructure fault and silently escalates to Google Cloud Vertex AI (`gemini-3.5-flash`), burning trial credits.
* **Impact:** Undetected cost leakage and latency regression during testing and debugging.

---

## 3. Prioritized Recommendations & Action Plan

| Priority | Component | Recommendation | Impact |
| :---: | :--- | :--- | :--- |
| **P0** | [`doorcheck.py`](file:///c:/work/commandcenter/hearth/callers/doorcheck.py) | Fix default backend boolean evaluation to check `entry.get("awake")` for `openai` API. | Restores truthful door health check; clears permanent false alarm. |
| **P0** | Shell Environment | Inject `OMEN_ARC_TOKEN` into user profile or enforce [`with-gateway-env.cmd`](file:///c:/work/commandcenter/hearth/etc/with-gateway-env.cmd) in CLI workflows. | Prevents silent trial-credit burn on local inference calls. |
| **P1** | `cc-conductor` SSH | Configure `~/.ssh/config` on `cc-conductor` with `StrictHostKeyChecking accept-new` for `*.mshome.net`. | Eliminates headless build fan-out failures after Hyper-V reboots. |
| **P1** | AM4 / FX99 Samba | Re-enable / unblock port 445 on AM4 Wi-Fi and FX99 wired interface. | Clears hung Windows drive states (`A:`, `W:`, `S:`, `Y:`) and timeout stalls. |
| **P2** | [`summon.py`](file:///c:/work/commandcenter/hearth/toolsurface/summon.py), [`dream.py`](file:///c:/work/commandcenter/hearth/toolsurface/dream.py) | Deprecate `wake_am4` and retarget `dream` to OMEN's native [`hearth.imagegen`](file:///c:/work/commandcenter/hearth/imagegen) pipeline. | Cleans up ghost calls to defunct AM4 services. |
| **P2** | `claudefarm1` Auth | Authorize `cc-conductor`'s SSH public key in `claude@claudefarm1:~/.ssh/authorized_keys`. | Restores VM-to-VM parity across all builder nodes. |

---

## 4. Remediation Recipes

### Recipe 1: Patch `doorcheck.py` Backend Health Evaluation
In [`hearth/callers/doorcheck.py`](file:///c:/work/commandcenter/hearth/callers/doorcheck.py#L380-L384):
```python
# Before
entry["default"] = backend.name == pool.default
if entry["default"]:
    default_up = bool(entry.get("up"))

# After
entry["default"] = backend.name == pool.default
if entry["default"]:
    if entry.get("api") == "openai":
        default_up = bool(entry.get("awake"))
    else:
        default_up = bool(entry.get("up"))
```

### Recipe 2: Fix `cc-conductor` SSH Config for Hyper-V Sibling DNS
On `cc-conductor` (`/home/claude/.ssh/config`):
```ssh
Host *.mshome.net cc-builder-* claudefarm1
    StrictHostKeyChecking accept-new
    ServerAliveInterval 15
    ServerAliveCountMax 4
```

### Recipe 3: Restore AM4 Samba on Wi-Fi
On AM4 (`ssh derek@192.168.12.233`):
```bash
sudo ufw allow in on wlp4s0 proto tcp to any port 445 comment "Samba from LAN"
sudo systemctl restart smbd
```

### Recipe 4: Retire or Guard `wake_am4` in `summon.py`
In [`hearth/toolsurface/summon.py`](file:///c:/work/commandcenter/hearth/toolsurface/summon.py):
Return `{ "ok": False, "retired": True, "reason": "B70 GPUs relocated to OMEN (ADR-0034); AM4 serves no LLM rungs." }` immediately when `wake_am4()` is called, preventing long SSH timeouts against defunct systemd units.
