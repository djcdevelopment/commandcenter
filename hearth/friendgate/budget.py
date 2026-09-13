"""Per-slot context of every llama-swap entry (from the yamls), and the payload budget check.

llama-server never rejects an over-long prompt; it truncates it silently (ADR-0018). The door refuses
over-budget payloads up front; the gate does the same, in tokens, from the same source of truth the
serving shape is launched from: `-c <total> -np <slots>` in each entry's cmd, per slot = total / slots
(`--kv-unified` entries share one pool, but a single request still cannot exceed it).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_ENTRY = re.compile(r'^  "?([A-Za-z0-9._-]+)"?:\s*$', re.M)
_CTX = re.compile(r"(?:^|\s)-c\s+(\d+)")
_SLOTS = re.compile(r"(?:^|\s)-np\s+(\d+)")
CHARS_PER_TOKEN = 3.2  # conservative for German + JSON; the estimate errs towards refusing early


def slot_context_from_yaml(text: str) -> dict[str, int]:
    """entry name -> tokens per slot, for every `models:` entry whose cmd names -c (and optionally -np)."""
    out: dict[str, int] = {}
    if "models:" not in text:
        return out
    body = text.split("models:", 1)[1]
    names = list(_ENTRY.finditer(body))
    for i, m in enumerate(names):
        block = body[m.end(): names[i + 1].start() if i + 1 < len(names) else len(body)]
        if m.group(1) in ("groups", "router", "healthCheckTimeout", "logLevel", "startPort", "macros", "hooks", "peers"):
            continue
        c = _CTX.search(block)
        if not c:
            continue
        n = _SLOTS.search(block)
        out[m.group(1)] = int(c.group(1)) // max(1, int(n.group(1)) if n else 1)
    return out


def slot_context_map(yaml_paths: list[Path]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for p in yaml_paths:
        if Path(p).is_file():
            merged.update(slot_context_from_yaml(Path(p).read_text(encoding="utf-8")))
    return merged


def estimate_prompt_tokens(payload: dict[str, Any]) -> int:
    """A cheap upper-ish estimate: the JSON length of messages + tools over CHARS_PER_TOKEN."""
    text = json.dumps(payload.get("messages", []), ensure_ascii=False) + json.dumps(payload.get("tools", []), ensure_ascii=False)
    return int(len(text) / CHARS_PER_TOKEN) + 64


def requested_completion_tokens(payload: dict[str, Any], default: int = 4096) -> int:
    for k in ("max_completion_tokens", "max_tokens"):
        v = payload.get(k)
        if isinstance(v, int) and v > 0:
            return v
    return default


def over_budget(payload: dict[str, Any], limit_tokens: int) -> tuple[bool, int, int]:
    """(refused?, estimated_prompt, completion) against a per-request token limit."""
    prompt = estimate_prompt_tokens(payload)
    completion = requested_completion_tokens(payload)
    return prompt + completion > limit_tokens, prompt, completion
