"""HEARTH tool provider: local inference (Stream H-B) + Banked Fire routing (P1 + P2).

Delegate a sub-thought to sunk compute mid-task: one blocking POST to a local
model, returned as text plus token/timing cost. Connection failure is a result,
not an exception — callers route around a cold worker. Pure stdlib (urllib).

Banked Fire (P1): behind the same tool signature, ``local_generate`` grows a
backend pool (hearth/etc/backends.toml). A caller can pin an ``endpoint`` (legacy,
wins outright), pin a ``backend`` by name, or pass a ``task`` tag and let the
router pick — e.g. task="research" prefers the dual-B70 oxen box. Two adapters:
Ollama ``/api/generate`` and OpenAI ``/v1/chat/completions`` (Bearer-auth). The
chosen backend and the reason are returned so every dispatch is an assay
observation on the ledger.

Banked Fire (P2): tag/task-routed (opportunistic) dispatches consult
``hearth.toolsurface.occupancy`` before landing on a backend — a busy (or
unreachable-probe/"unknown") backend is skipped in favor of the pool default
(omen-ollama), per the "mechnet jobs always win" design principle. The
occupancy reading at decision time rides in the result (``occupancy`` key)
alongside ``backend``/``routed_by``, so every dispatch is a complete assay
observation. A caller-pinned ``backend=`` name is never occupancy-skipped —
fail-open resolves unknown -> available for a deliberate pin.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, NamedTuple, Optional

from hearth.toolsurface.backends import BackendConfigError, load_pool, select_backend
from hearth.toolsurface.occupancy import check_occupancy

DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
ENDPOINT_ENV_VAR = "HEARTH_OLLAMA"


class _Target(NamedTuple):
    """The resolved dispatch target for one local_generate call."""
    endpoint: str          # normalized, no trailing slash
    api: str               # "ollama" | "openai" | "gemini"
    auth_token: Optional[str]
    auth_error: Optional[str]
    auth_env: Optional[str]  # env var name, for a helpful "token missing" error
    backend: Optional[str]   # declared backend name, or None for ad-hoc endpoints
    routed_by: str           # why this target was chosen (ledger provenance)
    occupancy: str            # "available" | "busy" | "unknown" at decision time
    settings: dict            # backend-specific provider settings


def _gcloud_executable() -> str:
    # Windows ships gcloud as gcloud.cmd; CreateProcess resolves bare names
    # without PATHEXT, so subprocess needs the shutil.which-resolved path.
    # Callers like doorcheck run in shells whose PATH lacks the Cloud SDK
    # (only gateway.cmd appends it), so fall back to the default install dirs.
    found = shutil.which("gcloud")
    if found:
        return found
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles")):
        if not base:
            continue
        candidate = os.path.join(base, "Google", "Cloud SDK",
                                 "google-cloud-sdk", "bin", "gcloud.cmd")
        if os.path.isfile(candidate):
            return candidate
    return "gcloud"


def _google_access_token(auth_env: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return a Google OAuth access token from env or local ADC via gcloud."""
    if auth_env:
        value = os.environ.get(auth_env)
        if value and value.strip():
            return value.strip(), None
    try:
        proc = subprocess.run(
            [_gcloud_executable(), "auth", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"could not obtain Google access token with gcloud: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return None, f"gcloud auth print-access-token failed: {detail or f'exit {proc.returncode}'}"
    token = proc.stdout.strip()
    if not token:
        return None, "gcloud auth print-access-token returned an empty token"
    return token, None


def _resolve_target(endpoint: str, task: Optional[str], backend: Optional[str]) -> _Target:
    """Apply the Banked Fire routing policy and return the dispatch target.

    Precedence: (1) an explicitly-passed endpoint wins outright, unchanged; then
    (2) backend/task routing through the pool (occupancy-checked, P2); then
    (3) the legacy default path, which still honors the HEARTH_OLLAMA override
    so existing behavior is intact.
    """
    pool = load_pool()

    # (1) Caller-pinned endpoint wins. Adopt a declared backend's api/auth if the
    # endpoint matches one; otherwise treat it as an ad-hoc Ollama endpoint.
    # An endpoint pin is as deliberate as a backend-name pin: no occupancy check.
    if endpoint != DEFAULT_ENDPOINT:
        matched = pool.by_endpoint(endpoint)
        if matched is not None:
            token, error = _google_access_token(matched.auth_env) if matched.api == "gemini" else (matched.token(), None)
            return _Target(endpoint.rstrip("/"), matched.api, token, error,
                           matched.auth_env, matched.name, "pinned-endpoint", "available",
                           matched.settings)
        return _Target(endpoint.rstrip("/"), "ollama", None, None, None, None,
                       "pinned-endpoint", "available", {})

    # (2) Route by backend name or task tag; occupancy consulted for tag routes.
    if backend or task:
        chosen, reason, occ = select_backend(pool, backend=backend, task=task,
                                             occupancy_check=check_occupancy)
        token, error = _google_access_token(chosen.auth_env) if chosen.api == "gemini" else (chosen.token(), None)
        return _Target(chosen.endpoint.rstrip("/"), chosen.api, token, error,
                       chosen.auth_env, chosen.name, reason, occ.get("occupancy", "available"),
                       chosen.settings)

    # (3) Legacy default: HEARTH_OLLAMA overrides the default endpoint. This path
    # never touches the banked-fire pool routing, so occupancy is not applicable.
    resolved = os.environ.get(ENDPOINT_ENV_VAR, DEFAULT_ENDPOINT).rstrip("/")
    named = pool.by_endpoint(resolved)
    return _Target(resolved, "ollama", None, None, None,
                   named.name if named else None, "default", "available", {})


def _post(url: str, payload: dict, timeout_s: int,
          headers: Optional[dict] = None) -> tuple[Optional[dict], Optional[str]]:
    """POST JSON and return (body, error). error is a string on failure, else None."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8")), None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"non-JSON response: {exc}"


def _generate_ollama(target: _Target, prompt: str, model: str, system: Optional[str],
                     max_tokens: int, timeout_s: int) -> dict:
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if system:
        payload["system"] = system

    started = time.monotonic()
    body, error = _post(f"{target.endpoint}/api/generate", payload, timeout_s)
    if error is not None:
        return {"ok": False, "error": error, "endpoint": target.endpoint, "model": model}
    wall_ms = round((time.monotonic() - started) * 1000)

    total_ns = body.get("total_duration")
    return {
        "ok": True,
        "text": body.get("response", ""),
        "model": body.get("model", model),
        "endpoint": target.endpoint,
        "tokens_in": body.get("prompt_eval_count"),
        "tokens_out": body.get("eval_count"),
        "duration_ms": round(total_ns / 1e6) if total_ns else wall_ms,
    }


def _generate_openai(target: _Target, prompt: str, model: str, system: Optional[str],
                     max_tokens: int, timeout_s: int) -> dict:
    if not target.auth_token:
        return {"ok": False,
                "error": f"no auth token for {target.backend or target.endpoint}: "
                         f"set the {target.auth_env or 'auth'} env var on the gateway",
                "endpoint": target.endpoint, "model": model}

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": model, "messages": messages,
               "max_tokens": max_tokens, "stream": False}
    headers = {"Authorization": f"Bearer {target.auth_token}"}

    started = time.monotonic()
    body, error = _post(f"{target.endpoint}/v1/chat/completions", payload, timeout_s, headers)
    if error is not None:
        return {"ok": False, "error": error, "endpoint": target.endpoint, "model": model}
    wall_ms = round((time.monotonic() - started) * 1000)

    choices = body.get("choices") or [{}]
    text = (choices[0].get("message") or {}).get("content", "")
    usage = body.get("usage") or {}
    return {
        "ok": True,
        "text": text,
        "model": body.get("model", model),
        "endpoint": target.endpoint,
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
        "duration_ms": wall_ms,
    }


def _generate_gemini(target: _Target, prompt: str, model: str, system: Optional[str],
                     max_tokens: int, timeout_s: int) -> dict:
    if target.auth_error:
        return {"ok": False, "error": target.auth_error,
                "endpoint": target.endpoint, "model": model}
    if not target.auth_token:
        return {"ok": False,
                "error": f"no Google access token for {target.backend or target.endpoint}: "
                         "run `gcloud auth application-default login` or set "
                         f"{target.auth_env or 'a backend auth_env'}",
                "endpoint": target.endpoint, "model": model}

    project = (target.settings.get("project")
               or os.environ.get(target.settings.get("project_env", "GOOGLE_CLOUD_PROJECT"))
               or os.environ.get("GOOGLE_CLOUD_PROJECT_ID"))
    location = (target.settings.get("location")
                or os.environ.get(target.settings.get("location_env", "GOOGLE_CLOUD_LOCATION"))
                or "global")
    if not project:
        return {"ok": False,
                "error": "no Google Cloud project configured: set GOOGLE_CLOUD_PROJECT "
                         "or backend.settings.project",
                "endpoint": target.endpoint, "model": model}

    payload: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    headers = {"Authorization": f"Bearer {target.auth_token}"}
    url = (
        f"{target.endpoint}/v1/projects/{urllib.parse.quote(project, safe='')}"
        f"/locations/{urllib.parse.quote(location, safe='')}"
        f"/publishers/google/models/{urllib.parse.quote(model, safe='')}:generateContent"
    )

    started = time.monotonic()
    body, error = _post(url, payload, timeout_s, headers)
    if error is not None:
        return {"ok": False, "error": error, "endpoint": target.endpoint, "model": model}
    wall_ms = round((time.monotonic() - started) * 1000)

    candidates = body.get("candidates") or []
    parts = (((candidates[0] if candidates else {}).get("content") or {}).get("parts") or [])
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    usage = body.get("usageMetadata") or {}
    return {
        "ok": True,
        "text": text,
        "model": model,
        "endpoint": target.endpoint,
        "tokens_in": usage.get("promptTokenCount"),
        "tokens_out": usage.get("candidatesTokenCount"),
        "duration_ms": wall_ms,
    }


def local_generate(prompt: str, model: str | None = None,
                   endpoint: str = DEFAULT_ENDPOINT, system: str | None = None,
                   max_tokens: int | None = None, timeout_s: int = 120,
                   task: str | None = None, backend: str | None = None) -> dict:
    """Generate text from a configured inference backend.

    Routing (Banked Fire): pass ``task`` (e.g. "research") to prefer a tagged
    backend, or ``backend`` to pin one by name; an explicit ``endpoint`` still
    wins outright. A tag-routed backend that is busy (or has unreachable
    occupancy) is skipped in favor of the pool default (P2); a name-pinned
    backend is never occupancy-skipped. The chosen backend, routing reason, and
    occupancy-at-decision all ride in the result.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("model must be a non-empty string")
    if max_tokens is not None and (not isinstance(max_tokens, int) or max_tokens <= 0):
        raise ValueError("max_tokens must be a positive integer")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")

    try:
        target = _resolve_target(endpoint, task, backend)
    except BackendConfigError as exc:
        return {"ok": False, "error": f"routing failed: {exc}",
                "endpoint": endpoint, "model": model}

    # Resolve the backend record once for both model and output-budget defaults.
    pool_backend = load_pool().by_name(target.backend) if target.backend else None

    # Model default: caller's explicit model wins; else the backend's first
    # declared model; else the historical qwen3-coder default.
    if model is None:
        model = (pool_backend.models[0] if pool_backend and pool_backend.models
                 else "qwen3-coder:30b")

    # Output-budget default: caller's explicit max_tokens wins; else the
    # backend's declared settings.max_tokens; else the historical 1024. A
    # thinking model (e.g. the gcp-gemini-pro rung) can burn a small budget
    # entirely on hidden reasoning and return EMPTY text, so a rung sets a
    # generous floor here rather than relying on the caller to remember it.
    if max_tokens is None:
        setting = pool_backend.settings.get("max_tokens") if pool_backend else None
        try:
            max_tokens = int(setting) if setting is not None else 1024
        except (TypeError, ValueError):
            max_tokens = 1024
        if max_tokens <= 0:
            max_tokens = 1024

    if target.api == "openai":
        result = _generate_openai(target, prompt, model, system, max_tokens, timeout_s)
    elif target.api == "gemini":
        result = _generate_gemini(target, prompt, model, system, max_tokens, timeout_s)
    else:
        result = _generate_ollama(target, prompt, model, system, max_tokens, timeout_s)

    result["backend"] = target.backend
    result["routed_by"] = target.routed_by
    result["occupancy"] = target.occupancy
    result["max_tokens"] = max_tokens
    # JS1: thread the resolved model_id to the gateway wrapper for the ledger
    # event's `model` field via the _ledger_model convention (see gateway.py);
    # the wrapper pops this key before returning the result to the caller.
    result["_ledger_model"] = result.get("model", model)
    return result


def get_tools() -> list[Callable]:
    return [local_generate]
