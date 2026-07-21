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

from hearth.toolsurface._scope import resolve_in_scope, scope_root
from hearth.toolsurface.backends import (BackendConfigError, BackendRoutingRefusal,
                                         Pool, load_pool, select_backend)
from hearth.toolsurface.occupancy import check_occupancy

DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
# O5: the historical wall-clock budget, now only the FLOOR — a rung may declare
# settings.timeout_s to match its own measured decode rate (see _apply_defaults).
# Flat 1000s baseline (2026-07-21): premature cutoffs were costing more in
# wasted orchestration tokens/time than a generous ceiling costs in worst-case
# wait, and trial-credit usage is nowhere near the runway limit. Revisit once
# a few days of real duration_ms data (knowledge/capacity.json) show per-rung
# p90/p99 so this can go back to being individually tuned instead of uniform.
DEFAULT_TIMEOUT_S = 1000
ENDPOINT_ENV_VAR = "HEARTH_OLLAMA"

# Door-side file packing caps (bytes). Module globals so tests (and a future
# per-rung setting) can tune them without touching the packing logic.
FILES_PER_FILE_CAP = 256 * 1024
FILES_TOTAL_CAP = 1024 * 1024


def _trial_suppressed(pool: Pool) -> set[str]:
    """A4: the names of trial rungs that should leave opportunistic routing.

    Burn is read from knowledge/offload.json (per_class.trial, input+output —
    kept fresh by the knowledge_rebuild timer). No budget configured, no data,
    or unreadable data all fail OPEN (empty set): suppression only ever engages
    on positive evidence that the runway is nearly spent. Pins are unaffected
    (the caller never sees this set on the pinned path).
    """
    budget_val = pool.trial.get("budget_tokens")
    if not budget_val:
        return set()
    try:
        budget = int(budget_val)
    except (TypeError, ValueError):
        return set()
    try:
        reserve = int(pool.trial.get("reserve_tokens", 0))
    except (TypeError, ValueError):
        reserve = 0

    try:
        offload_path = resolve_in_scope("knowledge/offload.json")
    except (ValueError, OSError):
        return set()
    if not offload_path.is_file():
        return set()
    try:
        with open(offload_path, "r", encoding="utf-8") as fh:
            offload_data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return set()

    per_class = offload_data.get("per_class") or {}
    trial_data = per_class.get("trial") or {}
    burn = int(trial_data.get("tokens_in") or 0) + int(trial_data.get("tokens_out") or 0)

    if burn >= budget - reserve:
        return {b.name for b in pool.backends if b.cost_class() == "trial"}
    return set()


def _pack_files(files: list[str]) -> tuple[str, list[dict]]:
    """Resolve scope-guarded paths and pack their contents into <file> blocks.

    Caps are enforced on os.stat sizes BEFORE reading; any bad path (escape,
    missing, over-cap) raises ValueError so the caller's dispatch never starts.
    """
    primary = scope_root()
    packed_blocks = []
    files_packed = []
    total_bytes = 0
    for path in files:
        resolved = resolve_in_scope(path)
        if not resolved.is_file():
            raise ValueError(f"path is not an existing regular file: {path}")

        size = resolved.stat().st_size
        if size > FILES_PER_FILE_CAP:
            raise ValueError(f"file {path} size {size} exceeds cap {FILES_PER_FILE_CAP}")
        if total_bytes + size > FILES_TOTAL_CAP:
            raise ValueError(f"files total size {total_bytes + size} exceeds total cap {FILES_TOTAL_CAP}")

        total_bytes += size
        content = resolved.read_text(encoding="utf-8", errors="replace")
        # Primary-root (repo) files keep their repo-relative label; files from a
        # secondary scope root are labeled by absolute path so the model (and the
        # manifest reader) can see which repo they came from.
        if resolved.is_relative_to(primary):
            label = resolved.relative_to(primary).as_posix()
        else:
            label = resolved.as_posix()
        packed_blocks.append(f'<file path="{label}">\n{content}\n</file>')
        files_packed.append({"path": label, "bytes": size})

    return "\n\n".join(packed_blocks), files_packed


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


def _resolve_target(endpoint: str, task: Optional[str], backend: Optional[str],
                    payload_bytes: Optional[int] = None,
                    exclude: Optional[set[str]] = None,
                    tags: Optional[list[str]] = None) -> _Target:
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

    # (1b) Legacy escape hatch: HEARTH_OLLAMA redirects the default path. An
    # operator env override is as deliberate as a pin, so it wins over payload
    # routing — but only when no explicit backend/task signal was given.
    if not backend and not task:
        env_endpoint = os.environ.get(ENDPOINT_ENV_VAR)
        if env_endpoint and env_endpoint.rstrip("/") != DEFAULT_ENDPOINT:
            resolved = env_endpoint.rstrip("/")
            named = pool.by_endpoint(resolved)
            return _Target(resolved, "ollama", None, None, None,
                           named.name if named else None, "default", "available", {})

    # A4: trial rungs past their credit runway leave opportunistic routing.
    # Pins bypass (the pinned branch above never reads exclude).
    suppressed = _trial_suppressed(pool)
    if not backend and suppressed:
        exclude = (exclude or set()) | suppressed

    # (2) Route by backend name, task tag, or quality tags — or consult the
    # pool for any call carrying a payload size or an exclude set (A1/A2);
    # occupancy consulted for tag routes.
    if backend or task or tags or payload_bytes is not None or exclude:
        chosen, reason, occ = select_backend(pool, backend=backend, task=task,
                                             tags=tags,
                                             occupancy_check=check_occupancy,
                                             payload_bytes=payload_bytes,
                                             exclude=exclude)
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
                   max_tokens: int | None = None, timeout_s: int | None = None,
                   task: str | None = None, backend: str | None = None,
                   files: list[str] | None = None,
                   quality: str | None = None) -> dict:
    """Generate text from a configured inference backend.

    Routing (Banked Fire): pass ``task`` (e.g. "research") to prefer a tagged
    backend, or ``backend`` to pin one by name; an explicit ``endpoint`` still
    wins outright. A tag-routed backend that is busy (or has unreachable
    occupancy) is skipped in favor of the pool default (P2); a name-pinned
    backend is never occupancy-skipped. The chosen backend, routing reason, and
    occupancy-at-decision all ride in the result.

    Quality tiers (A3): ``quality="fast"`` (or omitted) uses the sunk-first
    ladder (default behavior); ``"good"`` prefers the near-free flash rung
    while trial credits last; ``"best"`` does NOT dispatch — it returns an
    ``ask:true`` result recommending the pro rung, which auto-routing never
    selects (deliberate pin only).

    File packing (repo-aware): pass ``files`` — repo-relative paths, or absolute
    paths under any HEARTH_SCOPE root (e.g. other repos beneath C:\\work) — to
    have the door pack their contents into <file path="..."> blocks ahead of the
    prompt — no need to paste file contents. Paths are scope-guarded by the
    HEARTH_SCOPE sandbox; capped at 256 KiB per file and 1 MiB total. The packed
    manifest rides the result as ``files_packed``/``files_bytes``.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("model must be a non-empty string")
    if max_tokens is not None and (not isinstance(max_tokens, int) or max_tokens <= 0):
        raise ValueError("max_tokens must be a positive integer")
    if timeout_s is not None and timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if quality is not None and quality not in ("fast", "good", "best"):
        raise ValueError("quality must be 'fast', 'good', or 'best'")

    if quality == "best":
        # A3 ASK: quality=best maps to the pro rung, which auto-routing never
        # selects (D3). Zero network calls, zero tokens — the caller decides.
        return {
            "ok": True, "ask": True,
            "recommendation": {
                "backend": "gcp-gemini-pro",
                "reason": "quality=best maps to the pro rung, which auto-routing "
                          "never selects (deliberate pin only; finite trial credits)",
            },
            "text": 'ASK: quality=best requires the gcp-gemini-pro rung. Re-call with '
                    'backend="gcp-gemini-pro" to confirm, or quality="good" for the flash rung.',
            "backend": None, "routed_by": "ask:quality-best", "occupancy": "n/a",
            "max_tokens": 0,
        }

    files_packed_list = None
    files_bytes = 0
    if files is not None:
        if not isinstance(files, list) or not all(isinstance(f, str) and f for f in files):
            raise ValueError("files must be a list of non-empty path strings")
        if files:
            packed_block, files_packed_list = _pack_files(files)
            prompt = f"{packed_block}\n\n{prompt}"
            files_bytes = sum(f["bytes"] for f in files_packed_list)

    # A1: the payload size the router decides with — computed AFTER packing, so
    # a files= call is judged by what actually ships, not the bare prompt.
    payload_bytes = len(prompt.encode("utf-8"))

    def _apply_defaults(t: _Target, m: Optional[str],
                        mt: Optional[int]) -> tuple[str, int, int]:
        # Model default: caller's explicit model wins; else the backend's first
        # declared model; else the historical qwen3-coder default. Output-budget
        # default: caller's explicit max_tokens wins; else the backend's declared
        # settings.max_tokens; else the historical 1024. A thinking model (e.g.
        # the gcp-gemini-pro rung) can burn a small budget entirely on hidden
        # reasoning and return EMPTY text, so a rung sets a generous floor here
        # rather than relying on the caller to remember it. Shared by the first
        # attempt and the A2 escalation retry so both default identically.
        #
        # O5: the wall-clock budget resolves the SAME way (caller -> rung ->
        # 120s). A rung's max_tokens and its measured decode rate together imply
        # how long a full-budget answer takes; without a per-rung timeout the
        # two disagree and a healthy-but-slow rung gets cut off mid-answer and
        # escalated (see backends.toml am4-moe: 8192 tok at ~21 tok/s needs far
        # more than the historical 120s default).
        pool_backend = load_pool().by_name(t.backend) if t.backend else None
        rm = m
        if rm is None:
            rm = (pool_backend.models[0] if pool_backend and pool_backend.models
                  else "qwen3-coder:30b")
        rmt = mt
        if rmt is None:
            setting = pool_backend.settings.get("max_tokens") if pool_backend else None
            try:
                rmt = int(setting) if setting is not None else 1024
            except (TypeError, ValueError):
                rmt = 1024
            if rmt <= 0:
                rmt = 1024
        rts = timeout_s
        if rts is None:
            setting = pool_backend.settings.get("timeout_s") if pool_backend else None
            try:
                rts = int(setting) if setting is not None else DEFAULT_TIMEOUT_S
            except (TypeError, ValueError):
                rts = DEFAULT_TIMEOUT_S
            if rts <= 0:
                rts = DEFAULT_TIMEOUT_S
        return rm, rmt, rts

    def _execute(t: _Target, m: str, mt: int, ts: int) -> dict:
        if t.api == "openai":
            return _generate_openai(t, prompt, m, system, mt, ts)
        if t.api == "gemini":
            return _generate_gemini(t, prompt, m, system, mt, ts)
        return _generate_ollama(t, prompt, m, system, mt, ts)

    call_tags = ["cloud-overflow"] if quality == "good" else None

    try:
        target = _resolve_target(endpoint, task, backend, payload_bytes=payload_bytes,
                                 tags=call_tags)
    except BackendRoutingRefusal as exc:
        refusal = exc.as_dict()
        return {"ok": False,
                "error": f"routing refused: {exc.reason_code}",
                "error_code": "routing_refusal",
                "routing_refusal": refusal,
                "payload_bytes": refusal["payload_bytes"],
                "required_context_bytes": refusal["required_context_bytes"],
                "endpoint": endpoint, "model": model}
    except BackendConfigError as exc:
        return {"ok": False, "error": f"routing failed: {exc}",
                "endpoint": endpoint, "model": model}

    resolved_model, resolved_max_tokens, resolved_timeout_s = _apply_defaults(
        target, model, max_tokens)
    result = _execute(target, resolved_model, resolved_max_tokens, resolved_timeout_s)

    result["backend"] = target.backend
    result["routed_by"] = (f"quality-{quality}:{target.routed_by}"
                           if quality is not None else target.routed_by)
    result["occupancy"] = target.occupancy
    result["max_tokens"] = resolved_max_tokens
    result["timeout_s"] = resolved_timeout_s

    # A2: ladder escalation — one climb max. A failed non-pinned dispatch
    # excludes the failed rung and re-routes once; a pin (endpoint or name) is
    # a deliberate operator choice and never escalates.
    if result.get("ok") is False and not target.routed_by.startswith("pinned"):
        exclude_set = {target.backend} if target.backend else set()
        try:
            second_target = _resolve_target(endpoint, task, backend,
                                            payload_bytes=payload_bytes,
                                            exclude=exclude_set,
                                            tags=call_tags)
            if second_target.backend != target.backend:
                second_model, second_max_tokens, second_timeout_s = _apply_defaults(
                    second_target, model, max_tokens)
                second_result = _execute(second_target, second_model, second_max_tokens,
                                         second_timeout_s)

                first_name = target.backend or "default"
                second_name = second_target.backend or "default"
                second_result["backend"] = second_target.backend
                second_result["routed_by"] = f"escalation:{first_name}->{second_name}"
                second_result["occupancy"] = second_target.occupancy
                second_result["max_tokens"] = second_max_tokens
                second_result["timeout_s"] = second_timeout_s
                second_result["escalation"] = {"from": first_name, "error": result.get("error")}

                result = second_result
                resolved_model = second_model
        except BackendConfigError:
            pass  # escalation could not route -> the original failure stands

    if files_packed_list is not None:
        result["files_packed"] = files_packed_list
        result["files_bytes"] = files_bytes
    # JS1: thread the resolved model_id to the gateway wrapper for the ledger
    # event's `model` field via the _ledger_model convention (see gateway.py);
    # the wrapper pops this key before returning the result to the caller.
    result["_ledger_model"] = result.get("model", resolved_model)
    return result


def get_tools() -> list[Callable]:
    return [local_generate]
