#!/usr/bin/env python3
"""Run true concurrent service-path probes through the AM4 oxen facade."""

from __future__ import annotations

import argparse
import json
import os
import signal
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path("/home/derek/am4-fleet-node")
CONFIG_DIR = Path.home() / ".config" / "am4-fleet"
DEFAULT_ENV_FILE = CONFIG_DIR / "oxen.env"
DEFAULT_TOKEN_FILE = CONFIG_DIR / "oxen.token"
DEFAULT_RESULTS_DIR = ROOT / "results"


def read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def token() -> str:
    if DEFAULT_TOKEN_FILE.exists():
        return DEFAULT_TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    authorized: bool = False,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    headers = {}
    body = None
    if authorized and token():
        headers["Authorization"] = f"Bearer {token()}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read().decode("utf-8")
            return response.status, json.loads(data) if data else None
    except urllib.error.HTTPError as exc:
        data = exc.read().decode("utf-8", errors="replace")
        try:
            decoded = json.loads(data) if data else None
        except json.JSONDecodeError:
            decoded = {"body": data}
        return exc.code, decoded
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc.reason)}


def make_context_prompt(target_tokens: int, question: str) -> str:
    target_chars = max(0, target_tokens * 4)
    line = (
        "Context block: AM4 oxen concurrent service probe. "
        "This line exists to create repeated attention load without semantic importance. "
        "Preserve position, but do not summarize it. "
    )
    parts: list[str] = []
    current = 0
    index = 0
    while current < target_chars:
        block = f"[{index:06d}] {line}\n"
        parts.append(block)
        current += len(block)
        index += 1
    parts.append("\nQuestion:\n")
    parts.append(question)
    return "".join(parts)


def launch_backend(env_overrides: dict[str, str], log_prefix: Path) -> subprocess.Popen[str]:
    cmd = [str(ROOT / "scripts" / "start-oxen-backend.sh")]
    env = os.environ.copy()
    env.update(read_env_file(DEFAULT_ENV_FILE))
    env.update(env_overrides)

    stdout_path = log_prefix.with_suffix(".backend.stdout.log")
    stderr_path = log_prefix.with_suffix(".backend.stderr.log")
    stdout_file = stdout_path.open("w", encoding="utf-8")
    stderr_file = stderr_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        stdout=stdout_file,
        stderr=stderr_file,
        env=env,
        text=True,
        start_new_session=True,
    )
    proc._stdout_file = stdout_file  # type: ignore[attr-defined]
    proc._stderr_file = stderr_file  # type: ignore[attr-defined]
    return proc


def stop_backend(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
    finally:
        stdout_file = getattr(proc, "_stdout_file", None)
        stderr_file = getattr(proc, "_stderr_file", None)
        if stdout_file is not None:
            stdout_file.close()
        if stderr_file is not None:
            stderr_file.close()


def read_log_tail(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    data = path.read_text(encoding="utf-8", errors="replace")
    return data[-limit:]


def wait_for_health(
    base_url: str,
    timeout_s: float,
    *,
    proc: subprocess.Popen[str] | None = None,
    log_prefix: Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    deadline = started + timeout_s
    last: Any = None
    while time.perf_counter() < deadline:
        if proc is not None:
            returncode = proc.poll()
            if returncode is not None:
                failure: dict[str, Any] = {
                    "ok": False,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    "backend_exited": True,
                    "returncode": returncode,
                    "last": last,
                }
                if log_prefix is not None:
                    failure["stdout_tail"] = read_log_tail(log_prefix.with_suffix(".backend.stdout.log"))
                    failure["stderr_tail"] = read_log_tail(log_prefix.with_suffix(".backend.stderr.log"))
                return failure
        status, payload = json_request(base_url + "/health", timeout=5)
        last = {"status": status, "payload": payload}
        if 200 <= status < 300:
            return {
                "ok": True,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "status": status,
                "payload": payload,
            }
        time.sleep(2)
    return {
        "ok": False,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "last": last,
    }


def wait_for_ready(alias: str, timeout_s: float) -> dict[str, Any]:
    started = time.perf_counter()
    deadline = started + timeout_s
    encoded = urllib.parse.quote(alias, safe="")
    last: Any = None
    while time.perf_counter() < deadline:
        status, payload = json_request(
            f"http://127.0.0.1:8090/oxen/ready?alias={encoded}",
            authorized=True,
            timeout=35,
        )
        last = {"status": status, "payload": payload}
        if status == 200 and payload and payload.get("all_ready"):
            return {
                "ok": True,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "status": status,
                "payload": payload,
            }
        time.sleep(2)
    return {
        "ok": False,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "last": last,
    }


def stream_chat_request(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    launch_event: threading.Event,
    request_id: int,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token():
        headers["Authorization"] = f"Bearer {token()}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")

    launch_event.wait()
    started = time.perf_counter()
    first_chunk_at: float | None = None
    chunk_count = 0
    raw_chunks = 0
    finish_reason: str | None = None
    content_parts: list[str] = []

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for raw in response:
                raw_chunks += 1
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    chunk_count += 1
                    content_parts.append(text)
                    if first_chunk_at is None:
                        first_chunk_at = time.perf_counter()
                reason = choices[0].get("finish_reason")
                if reason:
                    finish_reason = reason
            ended = time.perf_counter()
            return {
                "request_id": request_id,
                "http_status": response.status,
                "ttft_ms": round(((first_chunk_at or ended) - started) * 1000, 1),
                "elapsed_ms": round((ended - started) * 1000, 1),
                "chunk_count": chunk_count,
                "raw_stream_lines": raw_chunks,
                "finish_reason": finish_reason,
                "content_chars": len("".join(content_parts)),
                "preview": "".join(content_parts)[:200],
            }
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return {
            "request_id": request_id,
            "http_status": exc.code,
            "ttft_ms": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error_body": error_body[:2000],
        }
    except urllib.error.URLError as exc:
        return {
            "request_id": request_id,
            "http_status": 0,
            "ttft_ms": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc.reason),
        }


def summarize_requests(requests: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in requests if r.get("http_status") == 200]
    ttfts = [r["ttft_ms"] for r in ok if r.get("ttft_ms") is not None]
    elapsed = [r["elapsed_ms"] for r in ok if r.get("elapsed_ms") is not None]
    return {
        "request_count": len(requests),
        "ok_count": len(ok),
        "error_count": len(requests) - len(ok),
        "ttft_ms": {
            "min": min(ttfts) if ttfts else None,
            "median": round(statistics.median(ttfts), 1) if ttfts else None,
            "max": max(ttfts) if ttfts else None,
        },
        "elapsed_ms": {
            "min": min(elapsed) if elapsed else None,
            "median": round(statistics.median(elapsed), 1) if elapsed else None,
            "max": max(elapsed) if elapsed else None,
        },
    }


def finalize(prefix: Path, result: dict[str, Any], code: int) -> int:
    result["finished_at"] = datetime.now(UTC).isoformat()
    result["backend_stdout_log"] = str(prefix.with_suffix(".backend.stdout.log"))
    result["backend_stderr_log"] = str(prefix.with_suffix(".backend.stderr.log"))
    path = prefix.with_suffix(".json")
    path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"artifact": str(path), "status": result.get("status")}, indent=2))
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", default="oxen-planner")
    parser.add_argument("--placement", default="single0", choices=["single0", "single1", "layer"])
    parser.add_argument("--ctx", type=int, default=131072)
    parser.add_argument("--parallel", type=int, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--prompt-depth", type=int, default=16384)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--health-timeout", type=int, default=900)
    parser.add_argument("--ready-timeout", type=int, default=1800)
    parser.add_argument("--stream-timeout", type=int, default=1800)
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR))
    parser.add_argument(
        "--question",
        default="State the active placement mode, then give a three-sentence summary of the tradeoff between throughput and context depth.",
    )
    args = parser.parse_args()

    placement_env = {
        "single0": {"DEVICE_LIST": "SYCL0", "SPLIT_MODE": "none"},
        "single1": {"DEVICE_LIST": "SYCL1", "SPLIT_MODE": "none"},
        "layer": {"DEVICE_LIST": "SYCL0,SYCL1", "SPLIT_MODE": "layer", "TENSOR_SPLIT": "1,1"},
    }[args.placement]

    started_at = datetime.now(UTC)
    run_id = (
        started_at.strftime("%Y-%m-%dT%H%M%SZ")
        + f"-{args.placement}-ctx{args.ctx}-p{args.parallel}-c{args.concurrency}-depth{args.prompt_depth}"
    )
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    prefix = results_dir / f"service-path-concurrency-{run_id}"

    prompt = make_context_prompt(args.prompt_depth, args.question)
    backend_env = {
        "CTX": str(args.ctx),
        "PARALLEL": str(args.parallel),
        "KV_TYPE_K": "q8_0",
        "KV_TYPE_V": "q8_0",
        "NO_HOST": "0",
    }
    backend_env.update(placement_env)

    result: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "alias": args.alias,
        "placement": args.placement,
        "backend_env": backend_env,
        "ctx": args.ctx,
        "parallel": args.parallel,
        "concurrency": args.concurrency,
        "prompt_depth_tokens_target": args.prompt_depth,
        "prompt_chars": len(prompt),
        "max_tokens": args.max_tokens,
        "question": args.question,
    }

    backend_proc: subprocess.Popen[str] | None = None
    try:
        backend_proc = launch_backend(backend_env, prefix)
        result["backend_pid"] = backend_proc.pid
        result["backend_health"] = wait_for_health(
            "http://127.0.0.1:8080",
            args.health_timeout,
            proc=backend_proc,
            log_prefix=prefix,
        )
        if not result["backend_health"]["ok"]:
            result["status"] = "backend-health-timeout"
            return finalize(prefix, result, 2)

        result["serve_ready"] = wait_for_ready(args.alias, args.ready_timeout)
        if not result["serve_ready"]["ok"]:
            result["status"] = "serve-ready-timeout"
            return finalize(prefix, result, 3)

        models_status, models_payload = json_request("http://127.0.0.1:8090/v1/models", authorized=True, timeout=10)
        result["models"] = {"status": models_status, "payload": models_payload}

        payload = {
            "model": args.alias,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": args.max_tokens,
            "temperature": 0,
            "stream": True,
        }
        launch_event = threading.Event()
        batch_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [
                executor.submit(
                    stream_chat_request,
                    "http://127.0.0.1:8090/v1/chat/completions",
                    payload,
                    timeout=args.stream_timeout,
                    launch_event=launch_event,
                    request_id=request_id,
                )
                for request_id in range(args.concurrency)
            ]
            launch_event.set()
            requests = [future.result() for future in futures]
        batch_ended = time.perf_counter()

        requests.sort(key=lambda item: int(item["request_id"]))
        result["requests"] = requests
        result["summary"] = summarize_requests(requests)
        result["summary"]["batch_elapsed_ms"] = round((batch_ended - batch_started) * 1000, 1)
        result["status"] = "ok"
        return finalize(prefix, result, 0)
    finally:
        stop_backend(backend_proc)


if __name__ == "__main__":
    raise SystemExit(main())
