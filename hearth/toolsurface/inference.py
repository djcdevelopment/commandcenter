"""HEARTH tool provider: local inference (Stream H-B).

Delegate a sub-thought to sunk compute mid-task: one blocking POST to an Ollama
/api/generate endpoint (stream=false). Endpoint override via the HEARTH_OLLAMA env var.
Connection failure is a result, not an exception — callers route around a cold worker.
Pure stdlib (urllib).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable

DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
ENDPOINT_ENV_VAR = "HEARTH_OLLAMA"


def local_generate(prompt: str, model: str = "qwen3-coder:30b",
                   endpoint: str = DEFAULT_ENDPOINT, system: str | None = None,
                   max_tokens: int = 1024, timeout_s: int = 120) -> dict:
    """Generate text from a local Ollama model; returns text plus token and timing cost."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")

    # HEARTH_OLLAMA overrides the default; an explicitly passed endpoint still wins.
    resolved_endpoint = endpoint
    if endpoint == DEFAULT_ENDPOINT:
        resolved_endpoint = os.environ.get(ENDPOINT_ENV_VAR, DEFAULT_ENDPOINT)
    resolved_endpoint = resolved_endpoint.rstrip("/")

    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if system:
        payload["system"] = system

    request = urllib.request.Request(
        f"{resolved_endpoint}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "endpoint": resolved_endpoint, "model": model}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"non-JSON response from Ollama: {exc}",
                "endpoint": resolved_endpoint, "model": model}
    wall_ms = round((time.monotonic() - started) * 1000)

    total_duration_ns = body.get("total_duration")
    return {
        "ok": True,
        "text": body.get("response", ""),
        "model": body.get("model", model),
        "endpoint": resolved_endpoint,
        "tokens_in": body.get("prompt_eval_count"),
        "tokens_out": body.get("eval_count"),
        "duration_ms": round(total_duration_ns / 1e6) if total_duration_ns else wall_ms,
    }


def get_tools() -> list[Callable]:
    return [local_generate]
