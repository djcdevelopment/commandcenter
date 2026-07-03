"""Local-model caller loop: drive an Ollama chat model with tool calling
against the HEARTH gateway.

The gateway tool list (MCP schemas) is converted to Ollama /api/chat "tools"
format; tool_calls in each assistant turn are executed through HearthClient
and fed back as role=tool messages until the model answers in plain text.

mcp-free by construction: HearthClient is imported lazily inside run_task,
so this module (and its tests, which inject a fake client) run on system
python. Ollama is reached with urllib via _post_json, which tests replace.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_MODEL = "qwen3-coder:30b"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
OLLAMA_ENV = "HEARTH_OLLAMA"


def _post_json(url: str, payload: dict, timeout: float = 300.0) -> dict:
    """POST payload as JSON, return the decoded JSON response. Tests patch this."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def mcp_tools_to_ollama(tools: list[dict]) -> list[dict]:
    """Convert HearthClient tool dicts ({name, description, input_schema}) to
    the Ollama /api/chat "tools" format."""
    converted = []
    for tool in tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description") or "",
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return converted


def _parse_tool_args(raw_args: object) -> tuple[dict | None, str | None]:
    """Return (args, error). Ollama usually sends a dict; some models emit a
    JSON string; anything else is malformed."""
    if isinstance(raw_args, dict):
        return raw_args, None
    if raw_args is None:
        return {}, None
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            return None, f"tool arguments were not valid JSON: {exc}"
        if isinstance(parsed, dict):
            return parsed, None
        return None, f"tool arguments must be a JSON object, got {type(parsed).__name__}"
    return None, f"tool arguments must be an object, got {type(raw_args).__name__}"


def run_task(
    task_prompt: str,
    model: str = DEFAULT_MODEL,
    endpoint: str | None = None,
    gateway_key: str = "dev-local",
    max_turns: int = 20,
    client=None,
    task_id: str | None = None,
) -> dict:
    """Run one task with a local model acting only through the gateway.

    Returns {ok, result_text, turns, tool_calls_made, tokens_in_total,
    tokens_out_total, error}.
    """
    if endpoint is None:
        endpoint = os.environ.get(OLLAMA_ENV, DEFAULT_OLLAMA)

    if client is None:
        from hearth.callers.client import HearthClient  # lazy: needs mcp SDK

        client = HearthClient(key=gateway_key, task_id=task_id)

    result = {
        "ok": False,
        "result_text": "",
        "turns": 0,
        "tool_calls_made": [],
        "tokens_in_total": 0,
        "tokens_out_total": 0,
        "error": None,
    }

    try:
        gateway_tools = client.list_tools_sync()
    except Exception as exc:  # gateway unreachable
        result["error"] = f"gateway tool list failed: {exc}"
        return result
    ollama_tools = mcp_tools_to_ollama(gateway_tools)

    messages: list[dict] = [{"role": "user", "content": task_prompt}]

    for _turn in range(max_turns):
        try:
            response = _post_json(
                f"{endpoint.rstrip('/')}/api/chat",
                {"model": model, "messages": messages, "tools": ollama_tools, "stream": False},
            )
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            result["error"] = f"ollama chat failed: {exc}"
            return result

        result["turns"] += 1
        result["tokens_in_total"] += int(response.get("prompt_eval_count") or 0)
        result["tokens_out_total"] += int(response.get("eval_count") or 0)

        message = response.get("message") or {}
        messages.append(message)
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            result["ok"] = True
            result["result_text"] = message.get("content") or ""
            return result

        for tool_call in tool_calls:
            function = (tool_call.get("function") or {}) if isinstance(tool_call, dict) else {}
            name = function.get("name") or "<missing>"
            args, parse_error = _parse_tool_args(function.get("arguments"))
            if parse_error is not None:
                print(f"[local_caller] malformed tool args for {name}: {parse_error}", file=sys.stderr)
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": json.dumps({"ok": False, "error": parse_error}),
                    }
                )
                continue
            result["tool_calls_made"].append(name)
            try:
                tool_result = client.call_sync(name, **args)
            except Exception as exc:
                tool_result = {"ok": False, "error": f"gateway call failed: {exc}"}
            messages.append(
                {
                    "role": "tool",
                    "tool_name": name,
                    "content": json.dumps(tool_result, default=str),
                }
            )

    result["error"] = f"max_turns ({max_turns}) exhausted without a final answer"
    return result


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m hearth.callers.local_caller <task prompt> [model]")
        return 2
    model = argv[2] if len(argv) > 2 else DEFAULT_MODEL
    outcome = run_task(argv[1], model=model)
    print(json.dumps(outcome, indent=2))
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
