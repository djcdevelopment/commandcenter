"""llama-swap v251 client (ADR-0040 Phase 2, ADR-0045 P3).

A thin, injectable HTTP client for the lifecycle owner on OMEN. Everything that can be
faked in tests is a callable seam (`fetch`, `clock`, `sleep`); nothing here imports
hearth.kernel. Verified endpoint semantics (upstream README + config.example.yaml, v251):

- ``GET /health``                       -> "OK"
- ``GET /running``                      -> ``{"running": [{"model": id, "state": ...}, ...]}``
- ``GET /upstream/{id}/health``         -> triggers a load; 503 while loading, 200 when ready
- ``POST /upstream/{id}/completion``    -> llama-server native completion (has ``timings``)
- ``GET /upstream/{id}/slots``          -> llama-server /slots
- ``POST /api/models/unload/{id}``      -> unload ONE model (PATH form). The bare
  ``POST /api/models/unload`` unloads ALL models -- including production under the
  2026-09-03 cutover -- so it is a separate, deliberately named method.
- ``GET /logs`` / ``GET /logs/stream/{id}`` -> upstream + proxy logs (llama-swap's own
  stdout carries upstream output only with ``logToStdout: both``).

Readiness rule (ADR-0043/0044, ff_ratecheck.py:94-109): port-open != model-ready and
``/health`` 200 != serving. ``wait_ready`` is READY only when the upstream answers 200
AND a 1-token completion returns a ``timings`` block.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

DEFAULT_ENDPOINT = "http://127.0.0.1:8081"     # llama-swap after the P13 cutover
DEFAULT_TOKEN_ENV = "OMEN_ARC_TOKEN"
DEFAULT_TIMEOUT_S = 8.0
PRODUCTION_PORTS = (8082, 8083, 8084)          # never addressed by this client's actuators

# fetch(url, method, body_bytes|None, timeout_s, headers) -> (status:int, text:str, error:str|None)
Fetch = Callable[[str, str, Optional[bytes], float, dict], tuple]


def _urlopen_fetch(url: str, method: str, body: Optional[bytes], timeout_s: float,
                   headers: dict) -> tuple:
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return int(response.status), response.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        return int(exc.code), text, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return 0, "", f"{type(exc).__name__}: {exc}"


@dataclass(frozen=True)
class RunningModel:
    model_id: str
    state: str

    @property
    def ready(self) -> bool:
        return self.state.lower() in ("ready", "running", "loaded")


@dataclass(frozen=True)
class LoadOutcome:
    ready: bool
    wall_s: float
    first_status: Optional[int] = None
    canary_timings: Optional[dict] = None
    error: Optional[str] = None
    attempts: int = 0


@dataclass
class LlamaSwapClient:
    """Injectable client; construct with ``fetch=`` in tests."""

    endpoint: str = DEFAULT_ENDPOINT
    fetch: Fetch = _urlopen_fetch
    token_env: Optional[str] = DEFAULT_TOKEN_ENV
    timeout_s: float = DEFAULT_TIMEOUT_S
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: list = field(default_factory=list)   # (method, path) audit trail, never bodies

    # -- plumbing ---------------------------------------------------------------------
    def _headers(self, with_bearer: bool = True, json_body: bool = False) -> dict:
        headers: dict = {}
        if json_body:
            headers["Content-Type"] = "application/json"
        token = os.environ.get(self.token_env) if (with_bearer and self.token_env) else None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _url(self, path: str) -> str:
        return self.endpoint.rstrip("/") + path

    def _get(self, path: str, timeout_s: Optional[float] = None, with_bearer: bool = True) -> tuple:
        self.calls.append(("GET", path))
        return self.fetch(self._url(path), "GET", None, timeout_s or self.timeout_s,
                          self._headers(with_bearer))

    def _post(self, path: str, payload: Optional[dict], timeout_s: Optional[float] = None,
              with_bearer: bool = True) -> tuple:
        self.calls.append(("POST", path))
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        return self.fetch(self._url(path), "POST", body, timeout_s or self.timeout_s,
                          self._headers(with_bearer, json_body=payload is not None))

    @staticmethod
    def _json(text: str):
        try:
            return json.loads(text) if text else None
        except ValueError:
            return None

    # -- read side --------------------------------------------------------------------
    def health(self) -> bool:
        status, text, _err = self._get("/health", with_bearer=False)
        return status == 200 and "OK" in (text or "").upper()

    def running(self) -> list[RunningModel]:
        status, text, err = self._get("/running", with_bearer=False)
        if err or status != 200:
            raise ConnectionError(f"llama-swap /running: {err or status}")
        data = self._json(text)
        items = data.get("running") if isinstance(data, dict) else data
        out: list[RunningModel] = []
        for item in items or []:
            if isinstance(item, dict):
                model_id = item.get("model") or item.get("id") or item.get("name")
                if model_id:
                    out.append(RunningModel(str(model_id), str(item.get("state", "")).lower()))
            elif isinstance(item, str):
                out.append(RunningModel(item, "ready"))
        return out

    def is_resident(self, model_id: str) -> bool:
        return any(m.model_id == model_id and m.ready for m in self.running())

    def upstream_health(self, model_id: str) -> int:
        """HTTP status of the upstream's /health (200 ready; 503 loading; 0 unreachable).
        Calling this on a not-yet-loaded model TRIGGERS its load."""
        status, _text, _err = self._get(f"/upstream/{model_id}/health", with_bearer=False)
        return status

    def slots(self, model_id: str) -> list[dict]:
        status, text, err = self._get(f"/upstream/{model_id}/slots")
        if err or status != 200:
            raise ConnectionError(f"/upstream/{model_id}/slots: {err or status}")
        data = self._json(text)
        return data if isinstance(data, list) else []

    def logs(self, max_chars: int = 262144) -> str:
        status, text, err = self._get("/logs", timeout_s=15.0, with_bearer=False)
        if err or status != 200:
            raise ConnectionError(f"/logs: {err or status}")
        return (text or "")[-max_chars:]

    # -- write side (never addresses PRODUCTION_PORTS directly) ------------------------
    def completion(self, model_id: str, prompt: str, n_predict: int = 1,
                   cache_prompt: bool = False, timeout_s: float = 120.0) -> dict:
        payload = {"prompt": prompt, "n_predict": int(n_predict), "temperature": 0,
                   "cache_prompt": bool(cache_prompt)}
        status, text, err = self._post(f"/upstream/{model_id}/completion", payload, timeout_s)
        data = self._json(text)
        if err or status != 200 or not isinstance(data, dict):
            return {"ok": False, "status": status, "error": err or f"HTTP {status}",
                    "text": (text or "")[:200]}
        return {"ok": True, "status": status, "timings": data.get("timings"),
                "content": data.get("content", ""), "raw": data}

    def slot_action(self, model_id: str, slot: int, action: str, filename: str,
                    timeout_s: float = 120.0) -> dict:
        """``POST /upstream/{id}/slots/{slot}?action=save|restore`` with ``{"filename": ...}``.

        Returns the server's JSON (``{"id_slot", "filename", "n_saved"|"n_restored", "n_written"|
        "n_read", "timings": {...}}`` on success) or ``{"ok": False, ...}``. The file format
        carries NO model identity (ADR-0040 P4) -- callers go through ``hearth.rotation.kv``,
        which refuses a cross-model restore before this is ever reached.
        """
        if action not in ("save", "restore", "erase"):
            raise ValueError(f"unknown slot action {action!r}")
        status, text, err = self._post(f"/upstream/{model_id}/slots/{int(slot)}?action={action}",
                                       {"filename": filename}, timeout_s)
        data = self._json(text)
        if err or status != 200 or not isinstance(data, dict):
            return {"ok": False, "status": status, "error": err or f"HTTP {status}",
                    "text": (text or "")[:200]}
        data.setdefault("ok", True)
        return data

    def unload(self, model_id: str) -> bool:
        """Unload ONE model -- the path form. (The bare endpoint unloads everything.)"""
        if not model_id:
            raise ValueError("unload() needs a model_id; use unload_all() to unload everything")
        status, _text, _err = self._post(f"/api/models/unload/{model_id}", None, with_bearer=False)
        return status in (200, 204)

    def unload_all(self) -> bool:
        """Unload EVERYTHING llama-swap holds -- including production after the cutover.
        Named separately so no caller reaches it by accident."""
        status, _text, _err = self._post("/api/models/unload", None, with_bearer=False)
        return status in (200, 204)

    def wait_ready(self, model_id: str, deadline_s: float = 300.0, poll_s: float = 2.0) -> LoadOutcome:
        """Block until the upstream answers 200 AND a 1-token completion carries timings.

        ``/health`` 200 while the model is still loading, and a 503-then-200 with no
        ``timings`` block, are both NOT ready (the 2026-08-29 lesson).
        """
        start = self.clock()
        first_status: Optional[int] = None
        attempts = 0
        last_error: Optional[str] = None
        while self.clock() - start < deadline_s:
            attempts += 1
            status = self.upstream_health(model_id)
            if first_status is None:
                first_status = status
            if status == 200:
                result = self.completion(model_id, "ok", n_predict=1, timeout_s=min(180.0, deadline_s))
                timings = result.get("timings") if result.get("ok") else None
                if isinstance(timings, dict) and timings:
                    return LoadOutcome(True, round(self.clock() - start, 3), first_status, timings,
                                       None, attempts)
                last_error = result.get("error") or "completion returned no timings block (served during load?)"
            else:
                last_error = f"upstream /health {status}"
            self.sleep(poll_s)
        return LoadOutcome(False, round(self.clock() - start, 3), first_status, None,
                           last_error or "deadline", attempts)
