#!/usr/bin/env python3
"""Small OpenAI-compatible alias facade for the AM4 Linux llama.cpp backend.

This intentionally mirrors the vllama/Hermes alias contract without depending on
the Windows-only vllama.exe lifecycle layer.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def backend_request(method: str, path: str, body: bytes | None = None, timeout: float = 10.0) -> tuple[int, dict[str, str], bytes]:
    host = env("AM4_BACKEND_HOST", "127.0.0.1")
    port = int(env("AM4_BACKEND_PORT", "8080"))
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    headers = {"Content-Type": "application/json"} if body else {}
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        hdrs = {k: v for k, v in resp.getheaders()}
        return resp.status, hdrs, data
    finally:
        conn.close()


def backend_health() -> dict[str, Any]:
    try:
        status, _, data = backend_request("GET", "/health", timeout=2.0)
        return {"ok": 200 <= status < 300, "status": status, "body": decode_json(data)}
    except Exception as exc:  # noqa: BLE001 - returned as readiness reason
        return {"ok": False, "error": str(exc)}


def decode_json(data: bytes) -> Any:
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return data.decode("utf-8", errors="replace")[:4096]


def configured_aliases() -> list[str]:
    raw = env("AM4_HERMES_ALIASES", "vllama-planner,hermes")
    return [item.strip() for item in raw.split(",") if item.strip()]


def token() -> str:
    return env("AM4_HERMES_TOKEN", "")


class Handler(BaseHTTPRequestHandler):
    server_version = "am4-hermes-facade/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.write_json(200, self.health_payload())
            return

        if not self.authorized():
            self.write_json(401, {"error": "missing or invalid bearer token"})
            return

        if parsed.path == "/v1/models":
            self.write_json(200, self.models_payload())
            return

        if parsed.path == "/vllama/runtime":
            self.write_json(200, self.health_payload())
            return

        if parsed.path == "/vllama/ready":
            query = parse_qs(parsed.query)
            aliases = query.get("alias", configured_aliases())
            payload = [self.probe_alias(alias) for alias in aliases]
            all_ready = all(item["ready"] for item in payload)
            self.write_json(200 if all_ready else 503, {"all_ready": all_ready, "aliases": payload})
            return

        self.write_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self.authorized():
            self.write_json(401, {"error": "missing or invalid bearer token"})
            return

        if parsed.path == "/v1/chat/completions":
            self.proxy_chat_completions()
            return

        if parsed.path in {"/vllama/load", "/vllama/swap", "/vllama/unload"}:
            self.write_json(
                409,
                {
                    "refused": True,
                    "reason": "backend lifecycle is managed by systemd on AM4",
                    "remedy": "sudo systemctl start|stop|restart am4-hermes-backend.service",
                },
            )
            return

        self.write_json(404, {"error": "not found"})

    def authorized(self) -> bool:
        expected = token()
        if not expected:
            return True
        supplied = self.headers.get("Authorization", "")
        return supplied == f"Bearer {expected}"

    def health_payload(self) -> dict[str, Any]:
        health = backend_health()
        return {
            "status": "ok",
            "node": "am4",
            "facade": "up",
            "backend": health,
            "aliases": configured_aliases(),
            "backend_base_url": f"http://{env('AM4_BACKEND_HOST', '127.0.0.1')}:{env('AM4_BACKEND_PORT', '8080')}",
            "time": int(time.time()),
        }

    def models_payload(self) -> dict[str, Any]:
        health = backend_health()
        ready = bool(health.get("ok"))
        return {
            "object": "list",
            "data": [
                {
                    "id": alias,
                    "object": "model",
                    "created": 0,
                    "owned_by": "am4",
                    "ready": ready,
                    "backend": {
                        "model": env("AM4_BACKEND_MODEL_ID", "Qwen3-30B-A3B-Instruct-2507-Q4_K_M"),
                        "base_url": f"http://{env('AM4_BACKEND_HOST', '127.0.0.1')}:{env('AM4_BACKEND_PORT', '8080')}",
                    },
                }
                for alias in configured_aliases()
            ],
        }

    def probe_alias(self, alias: str) -> dict[str, Any]:
        if alias not in configured_aliases():
            return {"alias": alias, "ready": False, "reason": "unknown alias"}
        body = json.dumps(
            {
                "model": env("AM4_BACKEND_MODEL_ID", "Qwen3-30B-A3B-Instruct-2507-Q4_K_M"),
                "messages": [{"role": "user", "content": "Reply with ok."}],
                "max_tokens": 1,
                "temperature": 0,
                "stream": False,
            }
        ).encode("utf-8")
        try:
            status, _, data = backend_request("POST", "/v1/chat/completions", body=body, timeout=30.0)
            return {"alias": alias, "ready": 200 <= status < 300, "status": status, "body": decode_json(data)}
        except Exception as exc:  # noqa: BLE001
            return {"alias": alias, "ready": False, "reason": str(exc)}

    def proxy_chat_completions(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length)
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception:
            self.write_json(400, {"error": "request body must be JSON"})
            return

        alias = payload.get("model")
        if alias not in configured_aliases():
            self.write_json(404, {"error": f"unknown model alias '{alias}'", "known": configured_aliases()})
            return

        payload["model"] = env("AM4_BACKEND_MODEL_ID", "Qwen3-30B-A3B-Instruct-2507-Q4_K_M")
        body = json.dumps(payload).encode("utf-8")

        host = env("AM4_BACKEND_HOST", "127.0.0.1")
        port = int(env("AM4_BACKEND_PORT", "8080"))
        conn = http.client.HTTPConnection(host, port, timeout=None)
        try:
            conn.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            self.send_response(resp.status)
            for key, value in resp.getheaders():
                if key.lower() not in HOP_BY_HOP:
                    self.send_header(key, value)
            self.end_headers()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except Exception as exc:  # noqa: BLE001
            self.write_json(502, {"error": f"upstream proxy error: {exc}"})
        finally:
            conn.close()

    def write_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=env("AM4_HERMES_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(env("AM4_HERMES_PORT", "8090")))
    args = parser.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"am4 hermes facade listening on http://{args.host}:{args.port}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

