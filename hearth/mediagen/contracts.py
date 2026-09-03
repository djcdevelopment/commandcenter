"""Shared, validated ArcServe contract generation for MediaGen."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from hearth.imagegen.session import ImageSessionController
from hearth.observation.telemetry import trace_span
from hearth.schemas.validate import validate

ARC_CHAT = "http://127.0.0.1:8082/v1/chat/completions"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def document_sha256(document_text: str) -> str:
    return hashlib.sha256(document_text.encode("utf-8")).hexdigest()


def query_arcserve(messages: list[dict], *, max_tokens: int = 4096) -> str:
    token = ImageSessionController._arc_token()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    payload = {
        "model": "qwen3-30b-a3b", "messages": messages, "temperature": 0.3,
        "max_tokens": max_tokens, "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        ARC_CHAT, data=json.dumps(payload).encode("utf-8"), headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError("ArcServe contract request failed") from exc
    try:
        return value["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("ArcServe returned an unsupported response") from exc


def generate_contract(
    *, template_name: str, schema_id: str, span_name: str, system_prompt: str,
    document_text: str, template_values: Optional[dict] = None,
    extra_validation: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Generate JSON, validate it, and allow one bounded correction attempt."""
    digest = document_sha256(document_text)
    environment = Environment(
        loader=FileSystemLoader(PROMPTS_DIR), undefined=StrictUndefined, autoescape=False
    )
    prompt = environment.get_template(template_name).render(
        document_text=document_text, document_sha256=digest, **(template_values or {})
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    with trace_span(span_name, attributes={"document.sha256": digest}) as span:
        last_error = "contract validation failed"
        for attempt in range(2):
            response_text = query_arcserve(messages)
            try:
                contract = json.loads(response_text)
                validate(contract, schema_id=schema_id)
                if extra_validation is not None:
                    extra_validation(contract)
                span.set_attribute("contract.attempts", attempt + 1)
                return contract
            except Exception as exc:
                last_error = str(exc)
                if attempt == 0:
                    messages.extend([
                        {"role": "assistant", "content": response_text},
                        {"role": "user", "content": (
                            "The JSON failed validation. Return a corrected JSON object only. "
                            "Validation error: " + last_error[:1000]
                        )},
                    ])
        span.set_attribute("contract.attempts", 2)
        raise ValueError("model output failed contract validation after one retry: " + last_error)
