# OMEN Linux Serving Stack Configuration

This directory tracks the live serving stack configuration for OMEN Linux (`omen-linux`).
It allows rebuilding and verifying the serving stack from git and detecting any configuration drift with:

```bash
python tools/ops/host_config.py --check
```

---

## Tracked Files

### 1. `systemd/` — User Systemd Units & Drop-ins
Located live at `~/.config/systemd/user/`:

- **Serving Services:**
  - `omen-vllm@.service`: Template service for vLLM XPU seats (`omen-vllm@0`, `omen-vllm@1`).
  - `omen-vllm-router.service`: HAProxy frontend service routing between vLLM seats and callers.
  - `hearth-production.service`: Primary HEARTH inference gateway.
  - `hearth-operator-web.service`: Operator web UI.
  - `hearth-ops-pages.service`: Ops status and documentation web server.

- **Timers & Scheduled Services:**
  - `bankedfire-drain.service` / `.timer`: 30-minute backlog drain loop.
  - `hearth-morning-report.service` / `.timer`: 06:45 daily status & health report.
  - `hearth-dashboard-snapshot.service` / `.timer`: Periodic dashboard snapshot generator.
  - `mechnet-linux-observer.service` / `.timer`: Mesh network node health & latency observer.

- **Drop-in Configurations:**
  - `omen-vllm@0.service.d/`: Drop-in configurations for seat 0 (quality/dense lane, e.g. Qwen3.8-27B):
    - Active: `stage0-recipe.conf`, `stage2-27b-mtp.conf`, `stage2b-batched.conf`, `stage2d-prefix-unit.conf`, `max-num-seqs.conf`
    - Staged / historical: `stage4-gemma4.conf.staged`, `stage5-qwen3-32b.conf.staged`, `stage6-devstral.conf.staged`, `stage7-devstral2507.conf.staged`, `max-model-len.conf.retired-20260927`
  - `omen-vllm@1.service.d/`: Drop-in configurations for seat 1 (fast/MoE lane, e.g. Qwen3-30B-A3B):
    - Active: `stage0-recipe.conf`, `max-model-len.conf`, `max-num-seqs.conf`
    - Retired: `stage1-35b-mtp.conf.retired`
  - `hearth-production.service.d/`:
    - `linux-routes.conf`: Environment drop-in pointing HEARTH at Linux backend and routing configurations.

### 2. `bin/` — Scripts and Helpers
Located live at `~/bin/`, `~/.local/bin/`, and `~/`:

- `start-vllm-seat.sh` (`~/bin/`): Seat startup script invoking vLLM with environment-derived parameters (`OMEN_*`). Supports `OMEN_DRY=1` for dry-run argv inspection.
- `wait-vllm-seat.sh` (`~/bin/`): Seat health check and readiness polling script.
- `preflight-vllm-seat.sh` (`~/bin/`): Preflight checks for Intel XPU runtime, dependencies, and GPU accessibility.
- `generate-api-env.py` (`~/bin/`): Safe generator for private bearer token environment files.
- `mount-windows.sh` (`~/bin/`): Mount helper for Windows partitions.
- `mount-omen-c-readonly.sh` (`~/`): Mounts OMEN Windows C: drive read-only via dislocker loop.
- `mount-omen-e-readonly.sh` (`~/`): Mounts OMEN Windows E: drive read-only.
- `omen-ai-mode` (`~/.local/bin/`): CLI for inspecting AI mode, active jobs, and GPU tenancy.
- `mechnet-readiness-probe` (`~/.local/bin/`): Probe script validating mesh endpoints and seat health.

### 3. `config/` — Service Configuration Files
Located live at `~/.config/omen-vllm/`:

- `haproxy.cfg`: HAProxy configuration routing ports `18091` (seat 0) and `18092` (seat 1) through unified frontend `18090`.

### 4. `hearth-production/` — Gateway Routing & Backends
Located live at `~/hearth-production/`:

- `backends-linux.toml`: Defines declared vLLM backends, contexts, ports, and aliases.
- `routing-families-linux.toml`: Task-family-to-backend routing definitions.
- `local-work-routes-linux.toml`: Fast/deep lane routing mappings for local work.

---

## Deliberately Excluded (Not in Git)

The following files are deliberately excluded to protect secrets and avoid committing host-local state:

- **Tokens & API keys:** `*.env` (`omen-api.env`, `fx99-api.env`, `hearth-backends.env`), `*.key` (`caller-key`, `bf6-dispatcher.key`), `callers*.json`, `web.token`.
- **Runtime data & ledgers:** `~/hearth-production/var/`, `~/hearth-production/runs/`, runtime logs (`*.log`), and `.bak-*` backup files.
- **Virtualenvs and caches:** Python `.venv`, `__pycache__`, pip/uv caches.

---

## Restore Order

To restore or deploy this configuration onto a fresh or recovered OMEN host:

1. **Prerequisites & Secrets:**
   - Ensure bearer env files exist in `~/.config/omen-vllm/` (generate via `bin/generate-api-env.py` if initializing fresh).
   - Ensure `~/bin` and `~/.local/bin` are on PATH.

2. **Copy Executables & Scripts:**
   ```bash
   cp host/omen-linux/bin/{start-vllm-seat.sh,wait-vllm-seat.sh,preflight-vllm-seat.sh,generate-api-env.py,mount-windows.sh} ~/bin/
   chmod +x ~/bin/*.sh ~/bin/*.py
   cp host/omen-linux/bin/{mount-omen-c-readonly.sh,mount-omen-e-readonly.sh} ~/
   chmod +x ~/mount-omen-*.sh
   cp host/omen-linux/bin/{omen-ai-mode,mechnet-readiness-probe} ~/.local/bin/
   chmod +x ~/.local/bin/*
   ```

3. **Copy Routing & Proxy Config:**
   ```bash
   mkdir -p ~/.config/omen-vllm ~/hearth-production
   cp host/omen-linux/config/haproxy.cfg ~/.config/omen-vllm/
   cp host/omen-linux/hearth-production/*.toml ~/hearth-production/
   ```

4. **Copy Units and Drop-ins:**
   ```bash
   mkdir -p ~/.config/systemd/user
   cp host/omen-linux/systemd/*.{service,timer} ~/.config/systemd/user/
   cp -r host/omen-linux/systemd/*.service.d ~/.config/systemd/user/
   ```

5. **Reload User Daemon:**
   ```bash
   systemctl --user daemon-reload
   ```

6. **Start Seats Sequentially (One Seat at a Time):**
   ```bash
   # Preflight GPU 0 and start seat 0
   ~/bin/preflight-vllm-seat.sh 0
   systemctl --user restart omen-vllm@0.service
   ~/bin/wait-vllm-seat.sh 0

   # Preflight GPU 1 and start seat 1
   ~/bin/preflight-vllm-seat.sh 1
   systemctl --user restart omen-vllm@1.service
   ~/bin/wait-vllm-seat.sh 1

   # Restart router and gateway
   systemctl --user restart omen-vllm-router.service
   systemctl --user restart hearth-production.service
   ```

7. **Verify:**
   ```bash
   python tools/ops/host_config.py --check
   omen-ai-mode status
   ```
