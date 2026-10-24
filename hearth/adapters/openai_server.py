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
import hashlib
import json
import os
import re
import threading
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
    "</jepa_context_pack>",
)

_DELIVERY_SOURCE_FILES_RE = re.compile(
    r"(?:^|\n)(SOURCE FILES \([^\n]*\):\n=== FILE [^\n]+ ===[\s\S]*\n=== END [^\n]+ ===)(?=\n|$)"
)
_SOURCEMAP_BLOCK_RE = re.compile(
    r"(?:^|\n)(=== FILE [^\n]+ ===[\s\S]*\n=== END [^\n]+ ===)(?=\n|$)"
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

    Supports:
    1. Explicit shared-pack markers (``=== END REPOSITORY SOURCE PACK ===``, ``[/ARCH_PACK]``,
       ``<!-- SHARED_CONTEXT_END -->``, ``</jepa_context_pack>``).
    2. ``local_generate(files=[...])`` ``<file path="...">...</file>`` blocks followed by task text.
    3. ``LocalWorkService`` delivery prompts (``one_call``, ``jepa_ast_sliced``, ``carry`` ``work``/``attach``,
       ``repair``, ``revision``) containing ``SOURCE FILES (...):\\n=== FILE ... === ... === END ... ===``:
       extracts the ``SOURCE FILES`` block as Turn 1 (``shared_context_pack``) and combines the request
       header + any trailing step/repair/revision instructions as Turn 2 (``task_instruction``), so all
       steps and briefs against the same files share an identical Turn 1 Mamba-2 state checkpoint.
    4. Direct ``SourceMap`` renders (``=== FILE ... === ... === END ... ===``) with surrounding task text.
    5. ``LocalWorkService`` code/patch prompts with ``CONTEXT PACK:\\n--- SOURCE ...`` (and optional
       trailing ``REPAIR:`` block): extracts ``CONTEXT PACK:\\n...`` as Turn 1 and the task/intent
       (+ any ``REPAIR:`` suffix) as Turn 2.

    Returns ``(shared_context_pack, task_instruction)`` if a supported delimiter is
    present and yields non-empty shared context + task instruction; otherwise returns ``None``.
    """
    if not isinstance(prompt, str) or not prompt:
        return None

    # Shape 6 (checked first so source_pack contents containing marker literals do not false-match):
    # LocalWorkService non-delivery JSON request_doc prompts:
    #   template + "\n\nREQUEST\n" + json.dumps(request_doc, sort_keys=True) [+ "\n\nREPAIR: ..."]
    # Also handles already-reordered Shape 6 prompts:
    #   template + "\n\nSHARED REPOSITORY CONTEXT\n...\n\nSOURCE PACK\n...\n\nREQUEST\n{...}"
    if "\n\nREQUEST\n{" in prompt:
        template_head, req_part = prompt.split("\n\nREQUEST\n", 1)
        if "\n\nSHARED REPOSITORY CONTEXT\n" in template_head and "\n\nSOURCE PACK\n" in template_head:
            shared_context_pack = template_head.strip()
            task_instruction = f"REQUEST\n{req_part.strip()}"
            if shared_context_pack and task_instruction:
                return shared_context_pack, task_instruction
        repair_tail = ""
        req_json_str = req_part
        if "\n\nREPAIR:" in req_part:
            req_json_str, repair_rest = req_part.split("\n\nREPAIR:", 1)
            repair_tail = f"REPAIR:{repair_rest.rstrip()}"
        try:
            req_doc = json.loads(req_json_str.strip())
        except Exception:
            req_doc = None
        if isinstance(req_doc, dict):
            raw_source_pack = req_doc.get("source_pack")
            if isinstance(raw_source_pack, str) and raw_source_pack.strip() and "intent" in req_doc:
                shared_meta = {
                    k: req_doc[k]
                    for k in ("declared_paths", "source_files")
                    if k in req_doc
                }
                shared_context_pack = (
                    f"{template_head.strip()}\n\n"
                    f"SHARED REPOSITORY CONTEXT\n"
                    f"{json.dumps(shared_meta, sort_keys=True)}\n\n"
                    f"SOURCE PACK\n{raw_source_pack.strip()}"
                )
                task_doc = {
                    k: v
                    for k, v in req_doc.items()
                    if k not in ("declared_paths", "source_files", "source_pack")
                }
                task_instruction = f"REQUEST\n{json.dumps(task_doc, sort_keys=True)}"
                if repair_tail:
                    task_instruction = f"{task_instruction}\n\n{repair_tail}"
                return shared_context_pack, task_instruction

    for marker in SHARED_PACK_END_MARKERS:
        idx = prompt.rfind(marker)
        if idx < 0:
            continue
        split_pos = idx + len(marker)
        shared_context_pack = prompt[:split_pos].strip()
        task_instruction = prompt[split_pos:].strip()
        if shared_context_pack and task_instruction:
            return shared_context_pack, task_instruction

    # Shape 2: <file path="...">...</file> blocks from inference._pack_files
    stripped = prompt.lstrip()
    if stripped.startswith("<file path=") and "</file>" in prompt:
        last_close = prompt.rfind("</file>")
        split_pos = last_close + len("</file>")
        shared_context_pack = prompt[:split_pos].strip()
        task_instruction = prompt[split_pos:].strip()
        if shared_context_pack and task_instruction:
            return shared_context_pack, task_instruction

    # Shape 3: LocalWorkService delivery prompts with SOURCE FILES (...):\n=== FILE ... === ... === END ... ===
    m_deliv = _DELIVERY_SOURCE_FILES_RE.search(prompt)
    if m_deliv is not None:
        shared_context_pack = m_deliv.group(1).strip()
        prefix = prompt[: m_deliv.start(1)].strip()
        suffix = prompt[m_deliv.end(1) :].strip()
        task_instruction = "\n\n".join(part for part in (prefix, suffix) if part)
        if shared_context_pack and task_instruction:
            return shared_context_pack, task_instruction

    # Shape 4: Direct SourceMap render (=== FILE ... === ... === END ... ===) with task text
    m_sm = _SOURCEMAP_BLOCK_RE.search(prompt)
    if m_sm is not None:
        shared_context_pack = m_sm.group(1).strip()
        prefix = prompt[: m_sm.start(1)].strip()
        suffix = prompt[m_sm.end(1) :].strip()
        task_instruction = "\n\n".join(part for part in (prefix, suffix) if part)
        if shared_context_pack and task_instruction:
            return shared_context_pack, task_instruction

    # Shape 5: LocalWorkService non-delivery code/patch prompts with CONTEXT PACK:\n--- SOURCE ...
    if "\n\nCONTEXT PACK:\n" in prompt:
        head, rest = prompt.split("\n\nCONTEXT PACK:\n", 1)
        if "--- SOURCE " in rest or "FILE: " in rest:
            if "\n\nREPAIR:\n" in rest:
                pack_body, repair_tail = rest.split("\n\nREPAIR:\n", 1)
                shared_context_pack = f"CONTEXT PACK:\n{pack_body.strip()}"
                task_instruction = f"{head.strip()}\n\nREPAIR:\n{repair_tail.strip()}"
            else:
                shared_context_pack = f"CONTEXT PACK:\n{rest.strip()}"
                task_instruction = head.strip()
            if shared_context_pack and task_instruction:
                return shared_context_pack, task_instruction

    return None


def checkpoint_messages_list(
    messages: list[dict[str, Any]],
    model_name: Optional[str] = None,
    backend_id: Optional[str] = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Apply Turn-Boundary Mamba-2 State Checkpointing to an explicit ``messages`` list
    (such as ``LocalWorkService`` ``carry`` ``work``, ``check``, and ``attach`` stages)
    when targeting a Hybrid Mamba-2 / ``llama-server`` seat, or reorder non-prefix shared
    context packs to token 0 of the first ``user`` message for non-Mamba vLLM APC seats.

    If the first ``user`` message contains a splittable shared context pack and is not
    already followed by ``TURN_CHECKPOINT_ACK``, splits that ``user`` message into:
        ``[{"role": "user", "content": shared_context_pack},
          {"role": "assistant", "content": TURN_CHECKPOINT_ACK},
          {"role": "user", "content": task_instruction}]``
    so ``work``, ``check``, and ``attach`` stages share the exact same Turn 1 Mamba-2
    checkpoint and ``check`` also reuses the ``work`` turn's KV prefix.
    """
    if not messages:
        return messages, False
    mamba = is_mamba_hybrid_target(model_name=model_name, backend_id=backend_id)

    out: list[dict[str, Any]] = []
    checkpointed = False
    for idx, msg in enumerate(messages):
        if (
            not checkpointed
            and isinstance(msg, dict)
            and msg.get("role") == "user"
            and isinstance(msg.get("content"), str)
        ):
            next_msg = messages[idx + 1] if idx + 1 < len(messages) else None
            if isinstance(next_msg, dict) and next_msg.get("role") == "assistant" and next_msg.get("content") == TURN_CHECKPOINT_ACK:
                checkpointed = True
                out.append(msg)
                continue
            split = split_shared_context_pack(msg["content"])
            if split is not None:
                shared_context_pack, task_instruction = split
                if mamba:
                    out.extend([
                        {"role": "user", "content": shared_context_pack},
                        {"role": "assistant", "content": TURN_CHECKPOINT_ACK},
                        {"role": "user", "content": task_instruction},
                    ])
                else:
                    reordered = (
                        f"{shared_context_pack}\n\n{task_instruction}"
                        if not msg["content"].lstrip().startswith(shared_context_pack)
                        else msg["content"]
                    )
                    out.append({**msg, "content": reordered})
                checkpointed = True
                continue
        out.append(msg)

    if mamba and (checkpointed or len(out) >= 3):
        return out, True
    return out, False


def build_turn_checkpointed_messages(
    prompt: str,
    system_prompt: Optional[str] = None,
    model_name: Optional[str] = None,
    backend_id: Optional[str] = None,
    image_url: Optional[str] = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Build ``/v1/chat/completions`` messages, splitting shared context packs into a
    2-turn checkpointed sequence when targeting a Hybrid Mamba-2 / ``llama-server`` seat,
    and reordering non-prefix shared context packs (such as ``LocalWorkService`` JSON
    ``request_doc`` or delivery ``SOURCE FILES`` prompts) to token 0 of the single user
    message for non-Mamba vLLM Automatic Prefix Caching seats.

    Returns ``(messages, cache_prompt_enabled)``.
    """
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    if not image_url:
        split = split_shared_context_pack(prompt)
        if split is not None:
            shared_context_pack, task_instruction = split
            if is_mamba_hybrid_target(model_name=model_name, backend_id=backend_id):
                messages.extend([
                    {"role": "user", "content": shared_context_pack},
                    {"role": "assistant", "content": TURN_CHECKPOINT_ACK},
                    {"role": "user", "content": task_instruction},
                ])
                return messages, True
            if not prompt.lstrip().startswith(shared_context_pack):
                prompt = f"{shared_context_pack}\n\n{task_instruction}"

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


_PREFIX_COALESCE_LOCK = threading.Lock()
_WARMED_PREFIX_KEYS: dict[tuple[str, str, str], float] = {}
_INFLIGHT_PREFIX_EVENTS: dict[tuple[str, str, str], threading.Event] = {}
_WARMED_PREFIX_TTL_S = 1800.0
_WARMED_PREFIX_MAX = 512
_SINGLE_FLIGHT_MAX_WAIT_S = 90.0


def reset_single_flight_prefix_cache() -> None:
    """Clear in-process single-flight cold-prefix state (used by unit tests and benchmarks)."""
    with _PREFIX_COALESCE_LOCK:
        _WARMED_PREFIX_KEYS.clear()
        for ev in _INFLIGHT_PREFIX_EVENTS.values():
            ev.set()
        _INFLIGHT_PREFIX_EVENTS.clear()


def extract_shared_prefix(
    messages: list[dict[str, Any]],
    prompt: str = "",
) -> Optional[str]:
    """Return the canonical shared prefix string if ``messages`` or ``prompt``
    contains a shared repository/architecture context pack; otherwise ``None``.
    """
    if not isinstance(messages, list) or not messages:
        return None

    for idx, msg in enumerate(messages):
        if (
            idx >= 1
            and isinstance(msg, dict)
            and msg.get("role") == "assistant"
            and msg.get("content") == TURN_CHECKPOINT_ACK
        ):
            return json.dumps(messages[:idx], sort_keys=True, ensure_ascii=False)

    system_parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role == "system" and isinstance(content, str):
            system_parts.append(content)
        elif role == "user" and isinstance(content, str):
            split = split_shared_context_pack(content)
            if split is None and prompt:
                split = split_shared_context_pack(prompt)
            if split is not None:
                shared_context_pack, _ = split
                sys_prefix = "\n".join(system_parts)
                return f"{sys_prefix}\n{shared_context_pack}" if sys_prefix else shared_context_pack
            break

    return None


def begin_single_flight_prefix(
    endpoint: str,
    model: str,
    messages: list[dict[str, Any]],
    prompt: str = "",
) -> tuple[Optional[tuple[str, str, str]], bool, Optional[threading.Event]]:
    """Register or join a single-flight cold-prefix wave.

    Returns ``(prefix_key, is_leader, event)``:
    - If ``messages`` has no shared context pack or the prefix is already warm:
      returns ``(None, False, None)``.
    - If the prefix is cold and no request is currently in-flight for it:
      marks ``prefix_key`` as in-flight with a new ``threading.Event`` and returns
      ``(prefix_key, True, event)``. The caller MUST call ``end_single_flight_prefix``
      in a ``finally:`` block.
    - If the prefix is cold and a leader request is currently in-flight:
      returns ``(prefix_key, False, event)`` so concurrent followers wait on ``event``
      before dispatching their HTTP POSTs.
    """
    shared_prefix = extract_shared_prefix(messages, prompt=prompt)
    if not shared_prefix:
        return None, False, None

    prefix_hash = hashlib.sha256(shared_prefix.encode("utf-8")).hexdigest()
    prefix_key = (endpoint.rstrip("/"), model or "", prefix_hash)
    now = time.monotonic()

    with _PREFIX_COALESCE_LOCK:
        warmed_at = _WARMED_PREFIX_KEYS.get(prefix_key)
        if warmed_at is not None:
            if (now - warmed_at) < _WARMED_PREFIX_TTL_S:
                return None, False, None
            _WARMED_PREFIX_KEYS.pop(prefix_key, None)

        existing_event = _INFLIGHT_PREFIX_EVENTS.get(prefix_key)
        if existing_event is not None:
            return prefix_key, False, existing_event

        event = threading.Event()
        _INFLIGHT_PREFIX_EVENTS[prefix_key] = event
        return prefix_key, True, event


def end_single_flight_prefix(
    prefix_key: Optional[tuple[str, str, str]],
    event: Optional[threading.Event],
    *,
    ok: bool = True,
) -> None:
    """Complete a leader's single-flight cold-prefix wave and wake waiting followers."""
    if prefix_key is None or event is None:
        return
    now = time.monotonic()
    with _PREFIX_COALESCE_LOCK:
        if ok:
            if len(_WARMED_PREFIX_KEYS) >= _WARMED_PREFIX_MAX:
                _WARMED_PREFIX_KEYS.pop(next(iter(_WARMED_PREFIX_KEYS)))
            _WARMED_PREFIX_KEYS[prefix_key] = now
        if _INFLIGHT_PREFIX_EVENTS.get(prefix_key) is event:
            _INFLIGHT_PREFIX_EVENTS.pop(prefix_key, None)
        event.set()


_BACKENDS_ENV_FILE = Path("/home/derek/.config/omen-vllm/hearth-backends.env")


def _load_default_omen_token(
    auth_env: Optional[str] = None,
    backend_id: Optional[str] = None,
    endpoint: str = "",
) -> Optional[str]:
    """Load auth token from environment or ~/.config/omen-vllm/{hearth-backends,omen-api}.env."""
    preferred_keys: list[str] = []
    if auth_env and auth_env != "OMEN_ARC_TOKEN":
        preferred_keys.append(auth_env)
    elif backend_id and "am4" in backend_id.lower() or "10.44.0.2" in endpoint:
        preferred_keys.append("AM4_VLLM_TOKEN")
    for k in ("OMEN_ARC_TOKEN", "AM4_VLLM_TOKEN", "VLLM_API_KEY", "OPENAI_API_KEY"):
        if k not in preferred_keys:
            preferred_keys.append(k)

    for env_key in preferred_keys:
        val = os.environ.get(env_key)
        if val:
            return val.strip()

    env_files = (_BACKENDS_ENV_FILE, _DEFAULT_ENV_FILE)
    parsed: dict[str, str] = {}
    for env_file in env_files:
        if not env_file.is_file():
            continue
        try:
            for raw in env_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                k, v = line.split("=", 1)
                k_clean = k.strip()
                v_clean = v.strip().strip('"').strip("'")
                if k_clean and v_clean and k_clean not in parsed:
                    parsed[k_clean] = v_clean
        except OSError:
            pass
    for env_key in preferred_keys:
        if env_key in parsed:
            return parsed[env_key]
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
            self.api_key = _load_default_omen_token(
                auth_env=auth_env,
                backend_id=backend_id,
                endpoint=self.endpoint,
            )

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

        prefix_key, is_leader, prefix_event = begin_single_flight_prefix(
            endpoint=self.endpoint,
            model=resolved_model,
            messages=payload["messages"],
            prompt=prompt,
        )
        single_flight_wait_ms = 0.0
        if not is_leader and prefix_event is not None:
            t_wait0 = time.perf_counter()
            wait_budget = min(float(resolved_timeout), _SINGLE_FLIGHT_MAX_WAIT_S)
            prefix_event.wait(timeout=max(0.1, wait_budget))
            single_flight_wait_ms = round((time.perf_counter() - t_wait0) * 1000.0, 2)

        res: dict[str, Any] = {"ok": False}
        try:
            if stream:
                res = self._post_stream(
                    url=url,
                    payload=payload,
                    headers=headers,
                    timeout_s=resolved_timeout,
                    resolved_model=resolved_model,
                    resolved_backend=resolved_backend,
                    turn_checkpointed=turn_checkpointed,
                    should_stop=should_stop,
                )
            else:
                res = self._post_json(
                    url=url,
                    payload=payload,
                    headers=headers,
                    timeout_s=resolved_timeout,
                    resolved_model=resolved_model,
                    resolved_backend=resolved_backend,
                    turn_checkpointed=turn_checkpointed,
                )
            if prefix_key is not None:
                res["single_flight_role"] = "leader" if is_leader else "follower"
                res["single_flight_wait_ms"] = single_flight_wait_ms
            return res
        finally:
            if is_leader:
                end_single_flight_prefix(
                    prefix_key,
                    prefix_event,
                    ok=bool(res.get("ok")),
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
