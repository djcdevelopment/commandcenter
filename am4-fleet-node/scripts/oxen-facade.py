#!/usr/bin/env python3
"""Small OpenAI-compatible alias facade for the AM4 Linux llama.cpp backend.

This intentionally mirrors the oxen alias contract without depending on
the Windows-only vllama.exe lifecycle layer.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import select
import socket
import sqlite3
import sys
import threading
import time
import uuid
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

_ADMISSION: dict[tuple[str, int], threading.Lock] = {}
_ADMISSION_GUARD = threading.Lock()
MAX_BODY = 4 * 1024 * 1024


def admission(backend: dict) -> threading.Lock:
    # Aliases are names, not extra physical serving slots.
    with _ADMISSION_GUARD:
        return _ADMISSION.setdefault((backend["host"], backend["port"]), threading.Lock())


def alias_status(alias: str) -> dict:
    if alias not in configured_aliases():
        return {"alias": alias, "ready": False, "reason": "unknown alias"}
    b = backend_for(alias)
    try:
        status, _, health = backend_request("GET", "/health", host=b["host"], port=b["port"], timeout=2)
        props_status, _, raw = backend_request("GET", "/props", host=b["host"], port=b["port"], timeout=2)
        props = decode_json(raw)
        settings = props.get("default_generation_settings", {}) if isinstance(props, dict) else {}
        context = settings.get("n_ctx", 0)
        return {"alias": alias, "ready": status == 200 and props_status == 200 and context > 0,
                "context_length": context, "parallel_slots": props.get("total_slots", 0),
                "physical_resource": f'am4:{b["host"]}:{b["port"]}',
                "model": props.get("model_path"), "status": status}
    except Exception as exc:
        return {"alias": alias, "ready": False, "reason": type(exc).__name__}


def guard_context(payload: dict, b: dict, state: dict) -> dict:
    """Count the engine-rendered template including tools; fail closed if unsupported."""
    budget = payload.get("max_completion_tokens", payload.get("max_tokens", 2048))
    if type(budget) is not int or not 1 <= budget <= 8192:
        raise ValueError("output budget must be an integer in 1..8192")
    if not isinstance(payload.get("messages"), list) or not payload["messages"]:
        raise ValueError("messages must be a nonempty list")
    if any(k in payload for k in ("prompt", "n_predict", "cache_prompt", "id_slot")):
        raise ValueError("native engine controls are not exposed")
    if payload.get("n", 1) != 1:
        raise ValueError("one completion per physical request")
    status, _, data = backend_request("POST", "/apply-template", json.dumps(payload).encode(),
                                      host=b["host"], port=b["port"], timeout=15)
    rendered = decode_json(data)
    if status != 200 or not isinstance(rendered, dict) or not isinstance(rendered.get("prompt"), str):
        raise RuntimeError("engine template rendering unavailable")
    status, _, data = backend_request("POST", "/tokenize", json.dumps({
        "content": rendered["prompt"], "add_special": True, "parse_special": True,
    }).encode(), host=b["host"], port=b["port"], timeout=15)
    counted = decode_json(data)
    if status != 200 or not isinstance(counted, dict) or not isinstance(counted.get("tokens"), list):
        raise RuntimeError("engine token count unavailable")
    tokens = len(counted["tokens"])
    if tokens + budget + 32 > state["context_length"]:
        raise ValueError(f"rendered prompt {tokens} + output {budget} exceeds context {state['context_length']}")
    payload["max_tokens"] = budget
    payload.pop("max_completion_tokens", None)
    return {"prompt_tokens": tokens, "output_budget": budget}


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def backend_request(method: str, path: str, body: bytes | None = None, timeout: float = 10.0,
                    host: str | None = None, port: int | None = None) -> tuple[int, dict[str, str], bytes]:
    host = host or env("AM4_BACKEND_HOST", "127.0.0.1")
    port = port if port is not None else int(env("AM4_BACKEND_PORT", "8080"))
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


def _global_backend() -> dict:
    return {"host": env("AM4_BACKEND_HOST", "127.0.0.1"),
            "port": int(env("AM4_BACKEND_PORT", "8080")),
            "model_id": env("AM4_BACKEND_MODEL_ID", "Qwen3-30B-A3B-Instruct-2507-Q4_K_M")}


def _read_alias_map_raw() -> dict:
    """Raw alias->backend map from AM4_ALIAS_BACKENDS (JSON env) or the JSON file
    AM4_ALIAS_BACKENDS_FILE (default ~/.config/am4-fleet/alias-backends.json), re-read
    live per request. Returns {} if neither is set. No fallback here (no recursion)."""
    raw = env("AM4_ALIAS_BACKENDS", "").strip()
    if not raw:
        path = env("AM4_ALIAS_BACKENDS_FILE",
                   os.path.expanduser("~/.config/am4-fleet/alias-backends.json"))
        try:
            if os.path.exists(path):
                raw = open(path, "r", encoding="utf-8").read().strip()
        except Exception:
            raw = ""
    if not raw:
        return {}
    try:
        m = json.loads(raw)
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def configured_aliases() -> list[str]:
    raw = env("AM4_OXEN_ALIASES", "oxen-planner,oxen")
    aliases = [item.strip() for item in raw.split(",") if item.strip()]
    # Union in aliases from the live map so adding a slot (write the JSON file) needs
    # no facade restart or env change.
    for a in _read_alias_map_raw().keys():
        if a not in aliases:
            aliases.append(a)
    return aliases


def alias_backends() -> dict:
    """alias -> {host, port, model_id}, filling per-alias gaps from the global backend.
    Falls back to every configured alias -> global backend (single-backend back-compat)."""
    g = _global_backend()
    m = _read_alias_map_raw()
    if m:
        return {a: {"host": b.get("host", g["host"]), "port": int(b.get("port", g["port"])),
                    "model_id": b.get("model_id", g["model_id"])} for a, b in m.items()}
    return {a: dict(g) for a in configured_aliases()}


def backend_for(alias: str) -> dict:
    return alias_backends().get(alias, _global_backend())


def token() -> str:
    return env("AM4_OXEN_TOKEN", "")


def hermes_token() -> str:
    path = env("AM4_HERMES_TOKEN_FILE", "")
    return Path(path).read_text().strip() if path and Path(path).is_file() else ""


class Handler(BaseHTTPRequestHandler):
    server_version = "am4-oxen-facade/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        # back-compat: legacy clients still call the /vllama/* lifecycle routes.
        parsed = parsed._replace(path=parsed.path.replace("/vllama/", "/oxen/", 1))
        if parsed.path == "/health":
            self.write_json(200, self.health_payload())
            return

        if not self.authorized():
            self.write_json(401, {"error": "missing or invalid bearer token"})
            return

        if parsed.path == "/v1/models":
            self.write_json(200, self.models_payload())
            return

        if parsed.path == "/oxen/runtime":
            self.write_json(200, self.health_payload())
            return

        if parsed.path == "/oxen/ready":
            query = parse_qs(parsed.query)
            aliases = query.get("alias", configured_aliases())
            payload = [self.probe_alias(alias) for alias in aliases]
            all_ready = all(item["ready"] for item in payload)
            self.write_json(200 if all_ready else 503, {"all_ready": all_ready, "aliases": payload})
            return

        self.write_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        # back-compat: legacy clients still call the /vllama/* lifecycle routes.
        parsed = parsed._replace(path=parsed.path.replace("/vllama/", "/oxen/", 1))
        if not self.authorized():
            self.write_json(401, {"error": "missing or invalid bearer token"})
            return

        if parsed.path == "/v1/chat/completions":
            self.proxy_chat_completions()
            return

        if parsed.path in {"/oxen/load", "/oxen/swap", "/oxen/unload"}:
            self.write_json(
                409,
                {
                    "refused": True,
                    "reason": "backend lifecycle is managed by systemd on AM4",
                    "remedy": "systemctl --user start|stop|restart b70-planner.service (or b70-critic.service)",
                },
            )
            return

        self.write_json(404, {"error": "not found"})

    def authorized(self) -> bool:
        expected = token()
        supplied = self.headers.get("Authorization", "")
        self.hermes_caller = False
        dedicated = hermes_token()
        if dedicated and hmac.compare_digest(supplied, f"Bearer {dedicated}"):
            self.hermes_caller = True
            return True
        return bool(expected) and hmac.compare_digest(supplied, f"Bearer {expected}")

    def health_payload(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "node": "am4",
            "facade": "up",
            "backend": {"ok": any(alias_status(a)["ready"] for a in configured_aliases())},
            "aliases": configured_aliases(),
            "backend_base_url": f"http://{env('AM4_BACKEND_HOST', '127.0.0.1')}:{env('AM4_BACKEND_PORT', '8080')}",
            "time": int(time.time()),
        }

    def models_payload(self) -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": alias,
                    "object": "model",
                    "created": 0,
                    "owned_by": "am4",
                    **alias_status(alias),
                    "backend": {
                        "model": backend_for(alias)["model_id"],
                        "base_url": f'http://{backend_for(alias)["host"]}:{backend_for(alias)["port"]}',
                    },
                }
                for alias in configured_aliases()
            ],
        }

    def probe_alias(self, alias: str) -> dict[str, Any]:
        # Readiness must not generate a token, queue behind a builder, or mutate KV.
        return alias_status(alias)

    def _legacy_active_probe(self, alias: str) -> dict[str, Any]:
        if alias not in configured_aliases():
            return {"alias": alias, "ready": False, "reason": "unknown alias"}
        b = backend_for(alias)
        body = json.dumps(
            {
                "model": b["model_id"],
                "messages": [{"role": "user", "content": "Reply with ok."}],
                "max_tokens": 1,
                "temperature": 0,
                "stream": False,
            }
        ).encode("utf-8")
        try:
            status, _, data = backend_request("POST", "/v1/chat/completions", body=body, timeout=30.0,
                                              host=b["host"], port=b["port"])
            return {"alias": alias, "ready": 200 <= status < 300, "status": status,
                    "backend": f'{b["host"]}:{b["port"]}', "model": b["model_id"], "body": decode_json(data)}
        except Exception as exc:  # noqa: BLE001
            return {"alias": alias, "ready": False, "backend": f'{b["host"]}:{b["port"]}', "reason": str(exc)}

    def proxy_chat_completions(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.write_json(400, {"error": "invalid content length"})
            return
        if not 0 < length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
            self.write_json(413, {"error": "bounded content length required"})
            return
        self.connection.settimeout(30)
        data = self.rfile.read(length)
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception:
            self.write_json(400, {"error": "request body must be JSON"})
            return

        if not isinstance(payload, dict):
            self.write_json(400, {"error": "request must be an object"})
            return
        alias = payload.get("model")
        if self.hermes_caller and alias != "am4-dense-27b":
            self.write_json(403, {"error": "Hermes credential permits only am4-dense-27b"})
            return
        if alias not in configured_aliases():
            self.write_json(404, {"error": f"unknown model alias '{alias}'", "known": configured_aliases()})
            return

        b = backend_for(alias)
        payload["model"] = b["model_id"]
        if self.hermes_caller:
            payload.setdefault("reasoning_effort", "none")
        host = b["host"]
        port = b["port"]
        lease = admission(b)
        if not lease.acquire(timeout=float(env("AM4_ADMISSION_WAIT_S", "30"))):
            self.write_json(429, {"error": "physical serving slot busy; retry after current request"})
            return
        conn = http.client.HTTPConnection(host, port, timeout=600)
        finished = threading.Event()
        sent_headers = False
        disconnected = threading.Event()
        attempt_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()
        response_capture = bytearray()
        requested = False
        completed = False
        status = None
        state = {}

        def cancel_watch() -> None:
            deadline = time.monotonic() + 600
            while not finished.wait(0.2):
                try:
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    gone = readable and not self.connection.recv(1, socket.MSG_PEEK)
                except OSError:
                    gone = True
                if gone or time.monotonic() > deadline:
                    disconnected.set()
                    if conn.sock:
                        try:
                            conn.sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    conn.close()
                    break

        try:
            state = alias_status(alias)
            if not state["ready"]:
                self.write_json(503, {"error": "selected alias is not ready", "state": state})
                return
            try:
                guard_context(payload, b, state)
            except ValueError as exc:
                self.write_json(400, {"error": str(exc)})
                return
            body = json.dumps(payload).encode("utf-8")
            threading.Thread(target=cancel_watch, daemon=True).start()
            requested = True
            conn.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            status = resp.status
            self.send_response(resp.status)
            for key, value in resp.getheaders():
                if key.lower() not in HOP_BY_HOP:
                    self.send_header(key, value)
            self.end_headers()
            sent_headers = True
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    completed = True
                    break
                if len(response_capture) < 8 * 1024 * 1024:
                    response_capture.extend(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception as exc:  # noqa: BLE001
            if not sent_headers and not disconnected.is_set():
                self.write_json(502, {"error": f"upstream proxy error: {type(exc).__name__}"})
        finally:
            finished.set()
            conn.close()
            lease.release()
            if self.hermes_caller and requested:
                try:
                    record_attempt(attempt_id, started_at, alias, state, bytes(response_capture),
                                   completed and status == 200, status)
                except Exception as exc:
                    # Never hide an accounting failure or send a second HTTP response.
                    self.log_message("attempt accounting failed: %s", type(exc).__name__)

    def write_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def record_attempt(attempt_id, started_at, alias, state, response, succeeded, status):
    usage = None
    documents = []
    try:
        documents.append(json.loads(response))
    except (ValueError, UnicodeError):
        for line in response.splitlines():
            if line.startswith(b"data:"):
                try:
                    documents.append(json.loads(line[5:]))
                except (ValueError, UnicodeError):
                    pass
    for doc in documents:
        if isinstance(doc, dict) and isinstance(doc.get("usage"), dict):
            raw = doc["usage"]
            usage = {"tokens_in": raw.get("prompt_tokens"), "tokens_out": raw.get("completion_tokens")}
    receipt = {"schema":"hermes.physical-attempt.v1", "accounting_owner":"direct",
        "execution_mode":"external", "run_id":"hermes-fleet-20260919",
        "attempt_id":attempt_id, "logical_call_id":attempt_id,
        "framework_run_id":"not_supplied_by_client", "supervisor_id":"am4-oxen-facade",
        "model_alias":alias, "started_at":started_at,
        "finished_at":datetime.now(timezone.utc).isoformat(), "phase":"task",
        "outcome":"succeeded" if succeeded else "failed" if status and status >= 400 else "unknown",
        "provider":{"execution_class":"local", "node":"am4", "state":state,
                    "identity_sha256":hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()},
        "usage":usage, "http_status":status,
        "correlation_note":"One physical HTTP attempt; logical retry grouping and framework session not provided."}
    path = Path(env("AM4_HERMES_OUTBOX", str(Path.home()/'.config/am4-fleet/hermes-attempts.sqlite')))
    with sqlite3.connect(path, timeout=10) as db:
        db.execute("CREATE TABLE IF NOT EXISTS attempts (attempt_id TEXT PRIMARY KEY, terminal TEXT, imported_job_id TEXT)")
        db.execute("INSERT INTO attempts(attempt_id,terminal) VALUES (?,?)", (attempt_id,json.dumps(receipt)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=env("AM4_OXEN_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(env("AM4_OXEN_PORT", "8090")))
    args = parser.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"am4 oxen facade listening on http://{args.host}:{args.port}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

