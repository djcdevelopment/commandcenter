#!/usr/bin/env bash
set -euo pipefail

with_nats=0
if [[ "${1:-}" == "--with-nats" ]]; then
  with_nats=1
fi

root="${HOME}/am4-fleet-node"
config_dir="${HOME}/.config/am4-fleet"
mkdir -p "${config_dir}"
chmod 700 "${config_dir}"

if [[ ! -f "${config_dir}/hermes.token" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32 > "${config_dir}/hermes.token"
  else
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > "${config_dir}/hermes.token"
  fi
  chmod 600 "${config_dir}/hermes.token"
fi

if [[ ! -f "${config_dir}/nats.password" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 36 > "${config_dir}/nats.password"
  else
    python3 -c 'import secrets; print(secrets.token_urlsafe(36))' > "${config_dir}/nats.password"
  fi
  chmod 600 "${config_dir}/nats.password"
fi

hermes_token="$(<"${config_dir}/hermes.token")"
nats_password="$(<"${config_dir}/nats.password")"

cat > "${config_dir}/hermes.env" <<EOF
AM4_HERMES_HOST=0.0.0.0
AM4_HERMES_PORT=8090
AM4_HERMES_TOKEN=${hermes_token}
AM4_HERMES_ALIASES=vllama-planner,hermes
AM4_BACKEND_HOST=127.0.0.1
AM4_BACKEND_PORT=8080
AM4_BACKEND_MODEL_ID=Qwen3-30B-A3B-Instruct-2507-Q4_K_M
BACKEND_KIND=sycl
LLAMA_SERVER=/home/derek/baseline/llama.cpp/build-sycl/bin/llama-server
MODEL_FILE=/home/derek/baseline/models/Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf
DEVICE_LIST=0,1
SPLIT_MODE=layer
TENSOR_SPLIT=1,1
CTX=131072
PARALLEL=1
KV_TYPE_K=q8_0
KV_TYPE_V=q8_0
NO_MMAP=0
NO_HOST=0
EOF
chmod 600 "${config_dir}/hermes.env"

cat > "${config_dir}/nats.conf" <<EOF
server_name: am4
port: 4222
http: 8222

jetstream {
  store_dir: "/data/jetstream"
  max_mem_store: 512Mb
  max_file_store: 20Gb
}

authorization {
  users = [
    { user: "commandcenter", password: "${nats_password}" }
  ]
}
EOF
chmod 600 "${config_dir}/nats.conf"

cat > "${config_dir}/nats.env" <<EOF
NATS_URL=nats://commandcenter:${nats_password}@am4.tail8e749c.ts.net:4222
NATS_MONITOR_URL=http://am4.tail8e749c.ts.net:8222
NATS_USER=commandcenter
NATS_PASSWORD=${nats_password}
EOF
chmod 600 "${config_dir}/nats.env"

chmod +x "${root}/scripts/"*.sh "${root}/scripts/"*.py

if [[ ! -d "${root}/.venv" ]]; then
  python3 -m venv "${root}/.venv"
fi
if ! "${root}/.venv/bin/python" -c 'import mcp' >/dev/null 2>&1; then
  "${root}/.venv/bin/python" -m pip install --upgrade pip >/dev/null
  "${root}/.venv/bin/python" -m pip install "mcp==1.28.1" >/dev/null
fi

sudo install -m 0644 "${root}/systemd/am4-hermes-facade.service" /etc/systemd/system/am4-hermes-facade.service
sudo install -m 0644 "${root}/systemd/am4-hermes-backend.service" /etc/systemd/system/am4-hermes-backend.service
sudo systemctl daemon-reload
sudo systemctl enable --now am4-hermes-facade.service
sudo systemctl disable am4-hermes-backend.service >/dev/null 2>&1 || true

if [[ "${with_nats}" == "1" ]]; then
  docker rm -f nats >/dev/null 2>&1 || true
  docker volume create am4-nats-data >/dev/null
  docker run -d \
    --name nats \
    --restart unless-stopped \
    -p 4222:4222 \
    -p 8222:8222 \
    -v "${config_dir}/nats.conf:/etc/nats/nats.conf:ro" \
    -v am4-nats-data:/data \
    nats:2-alpine \
    -c /etc/nats/nats.conf >/dev/null
else
  echo "Skipping NATS. Re-run with --with-nats when durable queue/fanout is needed."
fi

echo "AM4 fleet node installed."
echo "MCP stdio command: ssh derek@am4.tail8e749c.ts.net ${root}/.venv/bin/python ${root}/scripts/am4-mcp-server.py"
echo "Hermes facade token: ${config_dir}/hermes.token"
echo "NATS env: ${config_dir}/nats.env (optional; install with --with-nats)"
echo "Backend installed but not started. Start with: sudo systemctl start am4-hermes-backend.service"
