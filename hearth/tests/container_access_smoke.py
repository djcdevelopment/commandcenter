"""Ephemeral Docker-to-Hearth binding smoke test.

This never touches the durable gateway. It starts a temporary gateway on an
ephemeral non-loopback port, probes only unauthenticated /healthz from a
temporary Docker container, and always terminates the temporary process.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path


LISTEN_RE = re.compile(r"Uvicorn running on https?://[^\s:]+:(\d+)")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        callers = root / "callers.json"
        callers.write_text("{}", encoding="utf-8")
        log_path = root / "gateway.log"
        with log_path.open("wb") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "hearth.kernel.gateway",
                 "--host", "0.0.0.0", "--port", "0",
                 "--allow-non-loopback", "--no-timers",
                 "--callers", str(callers), "--ledger-dir", str(root / "ledger")],
                stdout=log, stderr=subprocess.STDOUT,
            )
        try:
            deadline = time.monotonic() + 30
            port = None
            while time.monotonic() < deadline and proc.poll() is None:
                text = log_path.read_text(encoding="utf-8", errors="replace")
                match = LISTEN_RE.search(text)
                if match:
                    port = int(match.group(1))
                    break
                time.sleep(0.1)
            if port is None:
                raise RuntimeError("temporary gateway did not announce a bound port")

            result = subprocess.run(
                ["docker", "run", "--rm", "curlimages/curl:8.10.1",
                 "--fail", "--silent", "--show-error",
                 f"http://host.docker.internal:{port}/healthz"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "Docker probe failed")
            payload = json.loads(result.stdout)
            if payload != {"status": "ok"}:
                raise RuntimeError(f"unexpected health payload: {payload!r}")
            print(f"container access smoke passed on ephemeral port {port}")
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())

