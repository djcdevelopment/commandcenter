"""Run the friend gate on loopback.

    hearth\\etc\\with-gateway-env.cmd fleet-worker-node\\.venv-omen\\Scripts\\python.exe -m hearth.friendgate

Environment (all optional): FRIENDGATE_PORT (8791), FRIENDGATE_UPSTREAM (http://127.0.0.1:8081),
FRIENDGATE_MIN_CONTEXT (65536), FRIENDGATE_MAX_CONCURRENT (1), FRIENDGATE_VAR (hearth/var/friendgate).
The rung's bearer comes from OMEN_ARC_TOKEN, sourced only by with-gateway-env.cmd; it is never logged.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

from .app import DEFAULT_VAR, GateConfig, build_app


def main() -> int:
    var = Path(os.environ.get("FRIENDGATE_VAR", str(DEFAULT_VAR)))
    cfg = GateConfig(
        upstream=os.environ.get("FRIENDGATE_UPSTREAM", "http://127.0.0.1:8081"),
        keys_path=var / "keys.json",
        usage_path=var / "usage.ndjson",
        min_context_tokens=int(os.environ.get("FRIENDGATE_MIN_CONTEXT", "65536")),
        max_concurrent_total=int(os.environ.get("FRIENDGATE_MAX_CONCURRENT", "1")),
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    port = int(os.environ.get("FRIENDGATE_PORT", "8791"))
    armed = "armed" if os.environ.get(cfg.token_env) else "NOT ARMED (no %s; every completion will answer 503)" % cfg.token_env
    print(f"friend gate: http://127.0.0.1:{port}/v1 -> {cfg.upstream}/v1  min_context={cfg.min_context_tokens} max_concurrent={cfg.max_concurrent_total}  {armed}")
    uvicorn.run(build_app(cfg), host="127.0.0.1", port=port, log_level="warning", access_log=False)  # no access log: bearers never hit a file here
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
