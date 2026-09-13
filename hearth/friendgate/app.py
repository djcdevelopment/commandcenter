"""The gate application (Starlette): auth -> eligibility -> budget -> limits -> forward -> usage row.

Build with `build_app(config)`; tests inject an httpx transport for the upstream and a fake `/running`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .budget import over_budget, slot_context_map
from .keys import FriendKey, KeyStore
from .usage import UsageLog

log = logging.getLogger("hearth.friendgate")

REPO = Path(__file__).resolve().parents[2]
DEFAULT_YAMLS = [REPO / "fleet/arcserve/llama-swap/omen.yaml", REPO / "fleet/arcserve/llama-swap/omen-night.yaml"]
DEFAULT_VAR = REPO / "hearth/var/friendgate"
STRIP_FIELDS = ("slot_id", "id_slot")  # server knobs a friend does not get to set
THINKING_DEFAULT = {"enable_thinking": False}  # the 3.8 models think by default; that eats an agent's output budget (campaign lesson)
STRIP_PREFIXES = ("speculative.",)
MAX_BODY = 32 * 1024 * 1024
RUNNING_CACHE_S = 5.0
_SSE_DATA = re.compile(rb"^data: (.*)$", re.M)


@dataclass
class GateConfig:
    upstream: str = "http://127.0.0.1:8081"
    token_env: str = "OMEN_ARC_TOKEN"
    keys_path: Path = DEFAULT_VAR / "keys.json"
    usage_path: Path = DEFAULT_VAR / "usage.ndjson"
    yaml_paths: list[Path] = field(default_factory=lambda: list(DEFAULT_YAMLS))
    min_context_tokens: int = 65_536  # an agent harness needs this much per slot; Hermes refused less
    max_concurrent_total: int = 1  # friends together; the owner keeps the other slot
    schedule_text: str = "friend hours are the night shape (a >=64k-context model resident); by day nothing eligible is loaded"
    transport: httpx.AsyncBaseTransport | None = None  # tests
    running_fetch: Callable[[], list[dict[str, Any]]] | None = None  # tests: replaces GET /running


def _err(status: int, message: str, code: str, **extra: Any) -> JSONResponse:
    body = {"error": {"message": message, "type": "friend_gate", "code": code, **extra}}
    headers = {"Retry-After": str(extra["retry_after"])} if "retry_after" in extra else None
    return JSONResponse(body, status_code=status, headers=headers)


class Gate:
    def __init__(self, cfg: GateConfig) -> None:
        self.cfg = cfg
        self.keys = KeyStore(cfg.keys_path)
        self.usage = UsageLog(cfg.usage_path)
        self.slot_ctx = slot_context_map(cfg.yaml_paths)
        self.client = httpx.AsyncClient(base_url=cfg.upstream, transport=cfg.transport, timeout=httpx.Timeout(900.0, connect=10.0))
        self._per_key: dict[str, asyncio.Semaphore] = {}
        self._total = asyncio.Semaphore(cfg.max_concurrent_total)
        self._running_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])

    # -- upstream state ----------------------------------------------------------------
    async def running(self) -> list[dict[str, Any]]:
        if self.cfg.running_fetch is not None:
            return self.cfg.running_fetch()
        ts, cached = self._running_cache
        if time.time() - ts < RUNNING_CACHE_S:
            return cached
        try:
            r = await self.client.get("/running")
            items = r.json().get("running", []) if r.status_code == 200 else []
        except Exception:  # noqa: BLE001 - upstream down = nothing eligible
            items = []
        self._running_cache = (time.time(), items)
        return items

    async def eligible_models(self) -> dict[str, int]:
        """model -> tokens per slot, for resident entries offering at least min_context_tokens."""
        out = {}
        for item in await self.running():
            name = item.get("model")
            if item.get("state") != "ready" or not name:
                continue
            ctx = self.slot_ctx.get(name)
            if ctx and ctx >= self.cfg.min_context_tokens:
                out[name] = ctx
        return out

    # -- auth ------------------------------------------------------------------------
    def authenticate(self, request: Request) -> FriendKey | JSONResponse:
        auth = request.headers.get("authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        key = self.keys.lookup(bearer)
        if key is None:
            return _err(401, "unknown key", "unauthorized")
        st = key.status()
        if st != "active":
            return _err(403, f"key {st}", st)
        return key

    def sem_for(self, key: FriendKey) -> asyncio.Semaphore:
        if key.key_id not in self._per_key:
            self._per_key[key.key_id] = asyncio.Semaphore(max(1, key.max_concurrent))
        return self._per_key[key.key_id]

    # -- handlers --------------------------------------------------------------------
    async def healthz(self, request: Request) -> Response:
        models = await self.eligible_models()
        return JSONResponse({"ok": True, "eligible_models": models, "min_context_tokens": self.cfg.min_context_tokens})

    async def models(self, request: Request) -> Response:
        key = self.authenticate(request)
        if isinstance(key, JSONResponse):
            return key
        eligible = await self.eligible_models()
        allowed = {m: c for m, c in eligible.items() if not key.models or m in key.models}
        if not allowed:
            return _err(503, f"off hours: no eligible model is loaded right now. {self.cfg.schedule_text}", "off_hours", retry_after=1800)
        data = [{"id": m, "object": "model", "owned_by": "omen", "context_window": min(c, key.max_context_tokens), "slot_context": c} for m, c in sorted(allowed.items())]
        return JSONResponse({"object": "list", "data": data})

    async def chat(self, request: Request) -> Response:
        key = self.authenticate(request)
        if isinstance(key, JSONResponse):
            return key
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return _err(413, "request body too large", "too_large")
        try:
            payload = json.loads((await request.body()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return _err(400, "body is not UTF-8 JSON", "bad_request")
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            return _err(400, "messages[] is required", "bad_request")
        model = str(payload.get("model") or "")
        eligible = await self.eligible_models()
        allowed = {m: c for m, c in eligible.items() if not key.models or m in key.models}
        if not allowed:
            return _err(503, f"off hours: no eligible model is loaded right now. {self.cfg.schedule_text}", "off_hours", retry_after=1800)
        if model not in allowed:
            return _err(404, f"model {model!r} is not available to you; choose one of {sorted(allowed)}", "model_not_found", available=sorted(allowed))
        limit = min(allowed[model], key.max_context_tokens)
        refused, prompt_est, completion = over_budget(payload, limit)
        if refused:
            return _err(400, f"payload over budget for {model}: ~{prompt_est} prompt + {completion} completion tokens > {limit} allowed per request; shorten the conversation or lower max_tokens",
                        "payload_over_budget_for_model", estimated_prompt_tokens=prompt_est, completion_tokens=completion, limit_tokens=limit)
        if self.usage.today(key.key_id) >= key.tokens_per_day:
            return _err(429, f"daily token budget ({key.tokens_per_day:,}) used up; resets at local midnight", "daily_budget_exhausted", retry_after=3600)
        for k in list(payload):
            if k in STRIP_FIELDS or k.startswith(STRIP_PREFIXES):
                payload.pop(k, None)
        ctk = payload.get("chat_template_kwargs")
        payload["chat_template_kwargs"] = {**THINKING_DEFAULT, **(ctk if isinstance(ctk, dict) else {})}  # thinking off unless the friend asks
        stream = bool(payload.get("stream"))
        if stream:
            opts = payload.get("stream_options") or {}
            opts["include_usage"] = True
            payload["stream_options"] = opts

        per_key = self.sem_for(key)
        if per_key.locked():
            return _err(429, "you already have a request in flight; one at a time", "busy", retry_after=20)
        if self._total.locked():
            return _err(429, "the cards are busy with another friend's request; try again shortly", "busy", retry_after=30)
        await per_key.acquire()
        await self._total.acquire()
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                per_key.release()
                self._total.release()

        token = os.environ.get(self.cfg.token_env, "")
        if not token:
            release()
            return _err(503, "gate is not armed with the rung's token (start it through with-gateway-env.cmd)", "gate_unarmed")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        req_id = uuid.uuid4().hex[:12]
        t0 = time.time()
        row: dict[str, Any] = {"request_id": req_id, "key_id": key.key_id, "irc_account": key.irc_account, "model": model, "stream": stream, "estimated_prompt_tokens": prompt_est}

        try:
            if not stream:
                r = await self.client.post("/v1/chat/completions", json=payload, headers=headers)
                body = r.json() if r.content else {}
                u = body.get("usage") or {} if isinstance(body, dict) else {}
                self.usage.record(**row, status=r.status_code, tokens_in=int(u.get("prompt_tokens", 0)), tokens_out=int(u.get("completion_tokens", 0)), duration_ms=int((time.time() - t0) * 1000))
                release()
                return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))
        except httpx.HTTPError as exc:
            self.usage.record(**row, status=502, tokens_in=0, tokens_out=0, duration_ms=int((time.time() - t0) * 1000), error=type(exc).__name__)
            release()
            return _err(502, f"upstream error: {type(exc).__name__}", "upstream_error")
        except Exception:
            release()
            raise

        # streaming: pass the SSE bytes through untouched, pick the usage out of the final chunk
        gate = self

        async def body_iter():
            status = 200
            tokens_in = tokens_out = 0
            try:
                async with gate.client.stream("POST", "/v1/chat/completions", json=payload, headers=headers) as r:
                    status = r.status_code
                    if status != 200:
                        raw = await r.aread()
                        yield raw
                        return
                    async for chunk in r.aiter_bytes():
                        for m in _SSE_DATA.finditer(chunk):
                            data = m.group(1).strip()
                            if data and data != b"[DONE]":
                                try:
                                    u = (json.loads(data).get("usage") or {})
                                    if u:
                                        tokens_in, tokens_out = int(u.get("prompt_tokens", tokens_in)), int(u.get("completion_tokens", tokens_out))
                                except (json.JSONDecodeError, AttributeError):
                                    pass
                        yield chunk
            except httpx.HTTPError as exc:
                status = 502
                yield f"data: {json.dumps({'error': {'message': f'upstream error: {type(exc).__name__}', 'code': 'upstream_error'}})}\n\n".encode()
            finally:
                gate.usage.record(**row, status=status, tokens_in=tokens_in, tokens_out=tokens_out, duration_ms=int((time.time() - t0) * 1000))
                release()

        return StreamingResponse(body_iter(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def not_found(self, request: Request) -> Response:
        return _err(404, "not found", "not_found")


def build_app(cfg: GateConfig | None = None) -> Starlette:
    gate = Gate(cfg or GateConfig())
    app = Starlette(routes=[
        Route("/healthz", gate.healthz, methods=["GET"]),
        Route("/v1/models", gate.models, methods=["GET"]),
        Route("/v1/chat/completions", gate.chat, methods=["POST"]),
        Route("/{path:path}", gate.not_found, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]),
    ])
    app.state.gate = gate
    return app
