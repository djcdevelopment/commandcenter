#!/usr/bin/env python3
"""Task 12 vision lane qualification script.

Sends one chart_diagram and one screenshot_grounded task through the live omen-dense-27b
seat (port 18095) to verify vision input works end-to-end on the XPU backend.

Usage:
    python3 tools/qualify/qualify_vision_lane.py [--port 18095] [--model qwen3.8-27b] [--all]

Runs two quick probes by default (chart-01 and screen-01). Pass --all to run all 16 tasks.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "campaign" / "qwen38" / "assay" / "assets"
TASKS_JSON = REPO / "campaign" / "qwen38" / "assay" / "tasks.json"

DEFAULT_PORT = 18095
DEFAULT_MODEL = "qwen3.8-27b"


def _api_key() -> str:
    key = os.environ.get("VLLM_API_KEY") or os.environ.get("OMEN_ARC_TOKEN")
    if not key:
        env_file = Path.home() / ".config" / "omen-vllm" / "omen-api.env"
        if env_file.is_file():
            for line in env_file.read_text().splitlines():
                if line.startswith("VLLM_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    return key or ""


def image_url(image_path: str) -> str:
    # Use PNG variant if SVG is referenced
    p = ASSETS / image_path.lstrip("assets/")
    if p.suffix == ".svg":
        png = p.with_suffix(".png")
        if png.exists():
            p = png
        else:
            raise FileNotFoundError(f"No PNG for {p}")
    data = base64.b64encode(p.read_bytes()).decode()
    mime = "image/png" if p.suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64,{data}"


def call_model(port: int, model: str, prompt: str, image_path: str) -> str:
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url(image_path)}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 256,
        "temperature": 0,
    }
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        return f"ERROR: {exc}"
    choices = data.get("choices", [])
    if not choices:
        return f"ERROR: no choices in response: {data}"
    return choices[0].get("message", {}).get("content", "").strip()


def check(actual: str, validator: dict) -> tuple[bool, str]:
    vtype = validator.get("type")
    expected = validator.get("expected")
    if vtype == "exact_text":
        norm_actual = actual.strip().strip('"').strip("'").strip()
        norm_exp = str(expected).strip()
        passed = norm_actual.lower() == norm_exp.lower()
        return passed, f"expected={expected!r} actual={actual!r}"
    elif vtype == "json_equal":
        try:
            parsed = json.loads(actual)
            # coerce numeric strings to int/float
            if isinstance(expected, dict) and isinstance(parsed, dict):
                coerced = {}
                for k, v in parsed.items():
                    try:
                        coerced[k] = type(list(expected.values())[0])(v)
                    except Exception:
                        coerced[k] = v
                passed = coerced == expected
            else:
                passed = parsed == expected
        except Exception as exc:
            return False, f"json parse error: {exc}; actual={actual!r}"
        return passed, f"expected={expected} actual={actual!r}"
    else:
        return True, f"unknown validator type {vtype!r}; actual={actual!r}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--all", action="store_true", dest="run_all",
                    help="Run all 16 vision tasks (default: 2 quick probes)")
    args = ap.parse_args()

    with open(TASKS_JSON) as f:
        d = json.load(f)
    tasks = [t for t in d.get("tasks", [])
             if t.get("family") in ["chart_diagram", "screenshot_grounded"]]

    if not args.run_all:
        # One from each family
        by_family: dict[str, list] = {}
        for t in tasks:
            by_family.setdefault(t["family"], []).append(t)
        tasks = [v[0] for v in by_family.values()]

    print(f"Vision qualification: {len(tasks)} task(s) against {args.model} at :{args.port}")
    print()

    results = []
    for t in tasks:
        tid = t["id"]
        family = t["family"]
        prompt = t["prompt"]
        image = t.get("image", "")
        validator = t.get("validator", {})

        sys.stdout.write(f"  {tid} ({family}): ... ")
        sys.stdout.flush()
        actual = call_model(args.port, args.model, prompt, image)
        passed, note = check(actual, validator)
        status = "PASS" if passed else "FAIL"
        print(f"{status}  {note}")
        results.append({"id": tid, "family": family, "passed": passed,
                         "actual": actual, "note": note})

    passed_count = sum(1 for r in results if r["passed"])
    total = len(results)
    print()
    print(f"Result: {passed_count}/{total} passed")
    print()
    print(json.dumps(results, indent=2))
    return 0 if passed_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
