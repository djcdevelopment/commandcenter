"""Door-side drafting: run one ``local_generate`` through HEARTH and write ``text`` straight to disk.

Why: a long draft (a journey doc, a living HTML page) should never transit the frontier
context that asked for it. This caller packs ``files=`` door-side, pins a rung, and
writes the result to ``--out``; the frontier session only reads the receipt line.

Usage (from C:\\work\\commandcenter, any interpreter with the mcp SDK — .venv-omen works):
  python -m hearth.callers.door_draft --prompt-file P.md --out OUT.md \
      --files docs/a.md docs/b.md --backend gcp-gemini-pro --task-id cc-XXXX [--max-tokens N]

Prints one JSON receipt line: ok, backend, model, routed_by, chars written, files_packed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _key_from_mcp_json() -> str:
    cfg = json.loads((REPO / ".mcp.json").read_text(encoding="utf-8"))
    return cfg["mcpServers"]["hearth"]["headers"]["X-Hearth-Key"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--prompt-file", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--backend", default="gcp-gemini-pro")
    ap.add_argument("--task-id", default=None)
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--quality", default=None)
    args = ap.parse_args(argv)

    from hearth.callers.client import HearthClient  # lazy: needs mcp SDK

    call: dict = {
        "prompt": args.prompt_file.read_text(encoding="utf-8"),
        "backend": args.backend,
    }
    if args.files:
        call["files"] = list(args.files)
    if args.task_id:
        call["task_id"] = args.task_id
    if args.max_tokens:
        call["max_tokens"] = args.max_tokens
    if args.quality:
        call["quality"] = args.quality

    client = HearthClient(key=_key_from_mcp_json())
    res = client.call_sync("local_generate", **call)

    # The tool result may arrive as a JSON string in ``text`` or as a parsed dict in ``json``.
    body = res.get("json")
    if not isinstance(body, dict):
        try:
            body = json.loads(res.get("text") or "")
        except (ValueError, TypeError):
            body = {"ok": bool(res.get("ok")), "text": res.get("text"), "raw": True}

    text = body.get("text") if isinstance(body, dict) else None
    ok = bool(body.get("ok")) and isinstance(text, str) and text.strip() != ""
    written = 0
    if ok:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
        written = len(text)

    receipt = {
        "ok": ok,
        "backend": body.get("backend"),
        "model": body.get("model"),
        "routed_by": body.get("routed_by"),
        "chars": written,
        "out": str(args.out) if ok else None,
        "files_packed": [f.get("path") if isinstance(f, dict) else f for f in (body.get("files_packed") or [])],
        "error": None if ok else (body.get("error") or body.get("error_code") or (text or "")[:300]),
    }
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
