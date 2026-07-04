#!/usr/bin/env bash
set -euo pipefail

run_xpu_smoke=0
if [[ "${1:-}" == "--xpu-smoke" ]]; then
  run_xpu_smoke=1
fi

token_file="${HOME}/.config/am4-fleet/oxen.token"
auth_header=()
if [[ -f "${token_file}" ]]; then
  auth_header=(-H "Authorization: Bearer $(<"${token_file}")")
fi

section() {
  printf '\n== %s ==\n' "$1"
}

section "identity"
hostname
id
tailscale ip -4 2>/dev/null || true

section "gpu pci"
lspci -nn | grep -Ei 'vga|3d|display|battlemage|graphics|intel' || true

section "render nodes"
ls -l /dev/dri 2>/dev/null || true
for node in /dev/dri/renderD128 /dev/dri/renderD129; do
  if [[ -e "${node}" ]]; then
    echo "--- ${node}"
    fuser -v "${node}" 2>&1 || true
  fi
done

section "torch xpu"
if [[ "${run_xpu_smoke}" == "1" && -d "${HOME}/venvs/torch-xpu" && -f "${HOME}/torch-xpu/torch_xpu_smoke.py" ]]; then
  source "${HOME}/venvs/torch-xpu/bin/activate"
  python "${HOME}/torch-xpu/torch_xpu_smoke.py"
elif [[ -d "${HOME}/venvs/torch-xpu" ]]; then
  echo "torch-xpu env present; pass --xpu-smoke to exercise both cards"
else
  echo "torch-xpu environment not found"
fi

section "docker"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true

section "ports"
ss -ltnp 2>/dev/null | sed -n '1,120p' || true

section "nats"
curl -fsS http://127.0.0.1:8222/varz 2>/dev/null | python3 -m json.tool 2>/dev/null | sed -n '1,80p' || echo "nats monitor unavailable"

section "oxen facade"
curl -fsS http://127.0.0.1:8090/health 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "facade unavailable"
if [[ ${#auth_header[@]} -gt 0 ]]; then
  curl -fsS "${auth_header[@]}" http://127.0.0.1:8090/v1/models 2>/dev/null | python3 -m json.tool 2>/dev/null || true
fi
