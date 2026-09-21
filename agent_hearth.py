"""HEARTH transport adapter for the existing JSON-action worker loop."""
import json
import sys
from hearth_client import HearthClient
from runner_presets import BASE_URL, MODEL, unwrap
import agent_openai as runner

TASK_ID = ''


def extract_action(text: str) -> dict | None:
    """Pull the first well-formed JSON object with an 'action' key out of the
    model's reply (tolerant of code fences / surrounding prose)."""
    # Prefer fenced blocks, else scan for balanced braces.
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates.append(text)
    for chunk in candidates:
        try:
            # Use JSON decoder to handle literal braces, escaped quotes, and nested values
            obj, end = json.JSONDecoder().raw_decode(chunk)
            if isinstance(obj, dict) and "action" in obj:
                return obj
        except json.JSONDecodeError:
            continue
    return None


if __name__ == '__main__':
    TASK_ID = sys.argv[sys.argv.index('--ta[REDACTED]')+1]
    runner.chat = chat
    runner.extract_action = extract_action
    raise SystemExit(runner.main())
