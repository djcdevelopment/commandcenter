"""OpenAI-compatible / llama-server adapter with Turn-Boundary Mamba-2 State Checkpointing.

Hybrid Mamba-2 (SSM) + Attention MoE models served by ``llama-server`` (such as
``NVIDIA-Nemotron-3.5-Bolt-74B-A9B`` on ``omen-nemotron-74b`` / ``127.0.0.1:18091``)
maintain sequential recurrent states (``h_t``) that cannot be sliced backwards to an
arbitrary mid-prompt token index unless a state checkpoint exists at that boundary.

When a prompt carries a shared repository source pack or NPU AST-sliced architecture
pack followed by a task-specific instruction:
- ``=== BEGIN REPOSITORY SOURCE PACK === ... === END REPOSITORY SOURCE PACK ===``
- ``[ARCH_PACK] ... [/ARCH_PACK]``
- ``<!-- SHARED_CONTEXT_END -->``

this adapter splits the user prompt right after the closing shared-context delimiter
into a 2-turn checkpointed conversation:
    [
        *([{"role": "system", "content": system_prompt}] if system_prompt else []),
        {"role": "user", "content": shared_context_pack},
        {"role": "assistant", "content": "Repository context indexed."},
        {"role": "user", "content": task_instruction},
    ]
and enables ``"cache_prompt": True`` on the wire so ``llama-server`` snapshots and
reuses the exact Mamba-2 + KV state at the end of Turn 1 across parallel subtasks.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

TURN_CHECKPOINT_ACK = "Repository context indexed."

SHARED_PACK_END_MARKERS: tuple[str, ...] = (
    "=== END REPOSITORY SOURCE PACK ===",
    "[/ARCH_PACK]",
    "<!-- SHARED_CONTEXT_END -->",
)

MAMBA_HYBRID_BACKENDS: frozenset[str] = frozenset({
    "omen-nemotron-74b",
    "omen-vllm",
    "omen-dense",
    "bolt74",
})

MAMBA_HYBRID_MODEL_TOKENS: tuple[str, ...] = (
    "nemotron",
    "bolt",
)

_DEFAULT_ENV_FILE = Path("/home/derek/.config/omen-vllm/omen-api.env")


def is_mamba_hybrid_target(
    model_name: Optional[str] = None,
    backend_id: Optional[str] = None,
) -> bool:
    """Return True when the target model or backend is a Hybrid Mamba-2 / llama-server seat."""
    m_low = (model_name or "").strip().lower()
    b_low = (backend_id or "").strip().lower()
    if any(tok in m_low for tok in MAMBA_HYBRID_MODEL_TOKENS):
        return True
    if b_low in MAMBA_HYBRID_BACKENDS or any(tok in b_low for tok in MAMBA_HYBRID_MODEL_TOKENS):
        return True
    return False


def split_shared_context_pack(prompt: str) -> Optional[tuple[str, str]]:
    """Split ``prompt`` at the end of a shared repository/architecture context pack.

    Returns ``(shared_context_pack, task_instruction)`` if a supported delimiter is
    present and followed by a non-empty task instruction; otherwise returns ``None``.
    """
    if not isinstance(prompt, str) or not prompt:
        return None
    for marker in SHARED_PACK_END_MARKERS:
        idx = prompt.find(marker)
        if idx < 0:
            continue
        split_pos = idx + len(marker)
        shared_context_pack = prompt[:split_pos].strip()
        task_instruction = prompt[split_pos:].strip()
        if shared_context_pack and task_instruction:
            return shared_context_pack, task_instruction
    return None


def build_turn_checkpointed_messages(
    prompt: str,
    system_prompt: Optional[str] = None,
    model_name: Optional[str] = None,
    backend_id: Optional[str] = None,
    image_url: Optional[str] = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Build ``/v1/chat/completions`` messages, splitting shared context packs into a
    2-turn checkpointed sequence when targeting a Hybrid Mamba-2 / ``llama-server`` seat.

    Returns ``(messages, cache_prompt_enabled)``.
    """
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    if not image_url and is_mamba_hybrid_target(model_name=model_name, backend_id=backend_id):
        split = split_shared_context_pack(prompt)
        if split is not None:
            shared_context_pack, task_instruction = split
            messages.extend([
                {"role": "user", "content": shared_context_pack},
                {"role": "assistant", "content": TURN_CHECKPOINT_ACK},
                {"role": "user", "content": task_instruction},
            ])
            return messages, True

    content: Any = (
        [
            {"type": "image_url", "image_url": {"url": image_url}},
            {"type": "text", "text": prompt},
        ]
        if image_url
        else prompt
    )
    messages.append({"role": "user", "content": content})
    return messages, False


def _load_default_omen_token() -> Optional[str]:
    """Load OMEN_ARC_TOKEN / VLLM_API_KEY from environment or ~/.config/omen-vllm/omen-api.env."""
    for env_key in ("OMEN_ARC_TOKEN", "VLLM_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(env_key)
        if val:
            return val.strip()
    if _DEFAULT_ENV_FILE.is_file():
        try:
            for raw in _DEFAULT_ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() in ("OMEN_ARC_TOKEN", "VLLM_API_KEY") and v.strip():
                    return v.strip().strip('"').strip("'")
        except OSError:
            pass
    return None


class OpenAIServerAdapter:
    """Adapter for OpenAI-compatible ``/v1/chat/completions`` seats (including ``llama-server``)."""

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:18091",
        model: str = "nemotron-3.5-bolt-74b-a9b",
        backend_id: Optional[str] = "omen-nemotron-74b",
        api_key: Optional[str] = None,
        auth_env: Optional[str] = "OMEN_ARC_TOKEN",
        timeout_s: int = 300,
        settings: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.backend_id = backend_id
        self.auth_env = auth_env
        self.timeout_s = timeout_s
        self.settings = dict(settings) if settings else {}
        if api_key is not None:
            self.api_key: Optional[str] = api_key
        elif auth_env and os.environ.get(auth_env):
            self.api_key = os.environ.get(auth_env)
        else:
            self.api_key = _load_default_omen_token()

    def build_payload(
        self,
        prompt: str = "",
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        backend_id: Optional[str] = None,
        max_tokens: int = 512,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        seed: Optional[int] = None,
        stream: bool = False,
        messages: Optional[list[dict[str, Any]]] = None,
        cache_prompt: Optional[bool] = None,
        thinking: Optional[bool] = None,
        response_schema: Optional[dict[str, Any]] = None,
        image_url: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Construct the ``/v1/chat/completions`` JSON request body."""
        resolved_model = model or self.model
        resolved_backend = backend_id if backend_id is not None else self.backend_id

        auto_cache_prompt = False
        if messages is None:
            built_messages, auto_cache_prompt = build_turn_checkpointed_messages(
                prompt=prompt,
                system_prompt=system,
                model_name=resolved_model,
                backend_id=resolved_backend,
                image_url=image_url,
            )
        else:
            built_messages = list(messages)

        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": built_messages,
            "max_tokens": max_tokens,
            "stream": bool(stream),
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if cache_prompt is not None:
            payload["cache_prompt"] = bool(cache_prompt)
        elif auto_cache_prompt:
            payload["cache_prompt"] = True

        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if seed is not None:
            payload["seed"] = seed

        chat_template_kwargs = self.settings.get("chat_template_kwargs")
        chat_template_kwargs = (
            dict(chat_template_kwargs) if isinstance(chat_template_kwargs, Mapping) else {}
        )
        if thinking is not None:
            chat_template_kwargs["enable_thinking"] = thinking
        if chat_template_kwargs:
            payload["chat_template_kwargs"] = chat_template_kwargs

        if response_schema is not None:
            schema_name = response_schema.get("title") or "structured_output"
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": response_schema,
                    "strict": True,
                },
            }

        if extra_body:
            payload.update(extra_body)

        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def warm_shared_prefix(
        self,
        prompt_or_pack: str,
        *,
        system: Optional[str] = None,
        slots: int = 8,
        timeout_s: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Pre-checkpoint the Turn-1 shared repository pack across ``slots`` parallel slots.

        By evaluating ``[{"role": "user", "content": shared_context_pack}]`` with
        ``max_tokens=1`` and ``cache_prompt=True`` in parallel across the server's slots,
        every slot snapshots the exact Mamba-2 recurrent state at the end of Turn 1
        before parallel subtask instructions arrive.
        """
        split = split_shared_context_pack(prompt_or_pack)
        shared_pack = split[0] if split is not None else prompt_or_pack.strip()
        warm_messages: list[dict[str, Any]] = []
        if system:
            warm_messages.append({"role": "system", "content": system})
        warm_messages.append({"role": "user", "content": shared_pack})

        def _warm_one(_idx: int) -> dict[str, Any]:
            return self.generate(
                messages=warm_messages,
                max_tokens=1,
                temperature=0.0,
                cache_prompt=True,
                stream=False,
                timeout_s=timeout_s or self.timeout_s,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, slots)) as pool:
            return list(pool.map(_warm_one, range(max(1, slots))))

    def generate(
        self,
        prompt: str = "",
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        backend_id: Optional[str] = None,
        max_tokens: int = 512,
        timeout_s: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        seed: Optional[int] = None,
        stream: bool = False,
        messages: Optional[list[dict[str, Any]]] = None,
        cache_prompt: Optional[bool] = None,
        thinking: Optional[bool] = None,
        response_schema: Optional[dict[str, Any]] = None,
        image_url: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        """Call ``/v1/chat/completions`` with automatic Turn-Boundary Mamba-2 checkpointing."""
        resolved_timeout = timeout_s if timeout_s is not None else self.timeout_s
        resolved_model = model or self.model
        resolved_backend = backend_id if backend_id is not None else self.backend_id

        payload = self.build_payload(
            prompt=prompt,
            system=system,
            model=resolved_model,
            backend_id=resolved_backend,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            stream=stream,
            messages=messages,
            cache_prompt=cache_prompt,
            thinking=thinking,
            response_schema=response_schema,
            image_url=image_url,
            extra_body=extra_body,
        )
        turn_checkpointed = (
            len(payload["messages"]) >= 3
            and payload["messages"][-2].get("role") == "assistant"
            and payload["messages"][-2].get("content") == TURN_CHECKPOINT_ACK
        )

        url = f"{self.endpoint}/v1/chat/completions"
        headers = self._headers()

        if stream:
            return self._post_stream(
                url=url,
                payload=payload,
                headers=headers,
                timeout_s=resolved_timeout,
                resolved_model=resolved_model,
                resolved_backend=resolved_backend,
                turn_checkpointed=turn_checkpointed,
                should_stop=should_stop,
            )
        return self._post_json(
            url=url,
            payload=payload,
            headers=headers,
            timeout_s=resolved_timeout,
            resolved_model=resolved_model,
            resolved_backend=resolved_backend,
            turn_checkpointed=turn_checkpointed,
        )

    def _post_json(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_s: int,
        resolved_model: str,
        resolved_backend: Optional[str],
        turn_checkpointed: bool,
    ) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "endpoint": self.endpoint,
                "model": resolved_model,
                "backend_id": resolved_backend,
                "turn_checkpointed": turn_checkpointed,
                "wire_request": payload,
            }
        wall_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        choices = body.get("choices") or [{}]
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
        finish_reason = choices[0].get("finish_reason")
        usage = body.get("usage") or {}
        timings = body.get("timings") or {}
        cached_tokens = timings.get(
            "cache_n",
            (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
        )
        prompt_tokens = usage.get("prompt_tokens") or (
            timings.get("prompt_n", 0) + cached_tokens
        )
        completion_tokens = usage.get("completion_tokens", timings.get("predicted_n", 0))
        return {
            "ok": True,
            "text": text,
            "reasoning": reasoning,
            "model": body.get("model") or resolved_model,
            "endpoint": self.endpoint,
            "backend_id": resolved_backend,
            "tokens_in": prompt_tokens,
            "tokens_out": completion_tokens,
            "cached_tokens": cached_tokens,
            "prompt_eval_tokens": timings.get("prompt_n", max(0, (prompt_tokens or 0) - cached_tokens)),
            "duration_ms": wall_ms,
            "finish_reason": finish_reason,
            "usage": usage,
            "timings": timings,
            "turn_checkpointed": turn_checkpointed,
            "wire_request": payload,
        }

    def _post_stream(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_s: int,
        resolved_model: str,
        resolved_backend: Optional[str],
        turn_checkpointed: bool,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        t_start = time.perf_counter()
        t_first_token: Optional[float] = None
        t_first_reasoning: Optional[float] = None
        t_first_content: Optional[float] = None
        t_last_token: Optional[float] = None
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason: Optional[str] = None
        usage: dict[str, Any] = {}
        timings: dict[str, Any] = {}
        returned_model = resolved_model
        chunks_count = 0

        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                for raw_line in resp:
                    if should_stop is not None and should_stop():
                        return {
                            "ok": False,
                            "error_code": "cancelled",
                            "error": "stream cancelled by should_stop",
                            "endpoint": self.endpoint,
                            "model": returned_model,
                            "backend_id": resolved_backend,
                            "turn_checkpointed": turn_checkpointed,
                            "wire_request": payload,
                        }
                    now = time.perf_counter()
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    chunk = json.loads(data_str)
                    if chunk.get("model"):
                        returned_model = chunk["model"]
                    choices = chunk.get("choices") or []
                    if choices:
                        c0 = choices[0]
                        if c0.get("finish_reason") is not None:
                            finish_reason = c0.get("finish_reason")
                        delta = c0.get("delta") or {}
                        r_tok = delta.get("reasoning") or delta.get("reasoning_content")
                        c_tok = delta.get("content")
                        if r_tok:
                            if t_first_token is None:
                                t_first_token = now
                            if t_first_reasoning is None:
                                t_first_reasoning = now
                            t_last_token = now
                            reasoning_parts.append(r_tok)
                            chunks_count += 1
                        if c_tok:
                            if t_first_token is None:
                                t_first_token = now
                            if t_first_content is None:
                                t_first_content = now
                            t_last_token = now
                            text_parts.append(c_tok)
                            chunks_count += 1
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    if chunk.get("timings"):
                        timings = chunk["timings"]
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "endpoint": self.endpoint,
                "model": returned_model,
                "backend_id": resolved_backend,
                "turn_checkpointed": turn_checkpointed,
                "wire_request": payload,
            }

        t_end = time.perf_counter()
        if t_first_token is None:
            t_first_token = t_end
        if t_last_token is None:
            t_last_token = t_end

        ttft_s = t_first_token - t_start
        pure_decode_s = max(1e-6, t_last_token - t_first_token)
        wall_s = max(1e-6, t_end - t_start)

        cached_tokens = timings.get(
            "cache_n",
            (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
        )
        prompt_tokens = usage.get("prompt_tokens") or (
            timings.get("prompt_n", 0) + cached_tokens
        )
        completion_tokens = usage.get("completion_tokens") or timings.get("predicted_n") or chunks_count
        pure_decode_tps = (max(1, completion_tokens - 1) / pure_decode_s) if completion_tokens > 1 else 0.0

        return {
            "ok": True,
            "text": "".join(text_parts),
            "reasoning": "".join(reasoning_parts),
            "model": returned_model,
            "endpoint": self.endpoint,
            "backend_id": resolved_backend,
            "tokens_in": prompt_tokens,
            "tokens_out": completion_tokens,
            "cached_tokens": cached_tokens,
            "prompt_eval_tokens": timings.get("prompt_n", max(0, (prompt_tokens or 0) - cached_tokens)),
            "ttft_ms": round(ttft_s * 1000.0, 2),
            "first_reasoning_ms": round((t_first_reasoning - t_start) * 1000.0, 2) if t_first_reasoning else None,
            "first_content_ms": round((t_first_content - t_start) * 1000.0, 2) if t_first_content else None,
            "pure_decode_ms": round(pure_decode_s * 1000.0, 2),
            "duration_ms": round(wall_s * 1000.0, 2),
            "pure_decode_tps": round(pure_decode_tps, 2),
            "e2e_tps": round(completion_tokens / wall_s, 2) if completion_tokens else 0.0,
            "finish_reason": finish_reason,
            "stream_chunks": chunks_count,
            "usage": usage,
            "timings": timings,
            "turn_checkpointed": turn_checkpointed,
            "wire_request": payload,
        }
