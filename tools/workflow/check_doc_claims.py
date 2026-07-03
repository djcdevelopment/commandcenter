"""
check_doc_claims.py — gate that verifies authored HTML doc claims against projected knowledge files.

Loads docs/doc-claims.json (the claim registry) and docs/doc-claims-waivers.json (waiver list),
evaluates each check against the referenced knowledge file, and prints a results table.
Exits nonzero if any un-waived claim FAILs.

Dot-path semantics: each segment separated by "." is a dict key. When the resolved value is a
LIST, the checker compares the LIST'S LENGTH against the check value, not the list itself.
This lets claims like "path": "candidates", "op": "gte", "value": 1 assert "at least one candidate
exists" by comparing len(candidates_list) >= 1.

Supported operators:
  gte   — actual >= value (or len(actual) >= value when actual is a list)
  eq    — actual == value (or len(actual) == value when actual is a list)
  exists — the path resolves to a non-None value (value field ignored)

Waiver expiry: a waiver with expires=null never expires; expires="YYYY-MM-DD" is compared to
today's UTC date — an expired waiver is treated as FAIL.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Repo root is two levels above this file (tools/workflow/check_doc_claims.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CLAIMS_FILE = _REPO_ROOT / "docs" / "doc-claims.json"
WAIVERS_FILE = _REPO_ROOT / "docs" / "doc-claims-waivers.json"


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _resolve_path(data: Any, dot_path: str) -> Any:
    """Walk dot_path segments through nested dicts. Raises KeyError on missing key."""
    current = data
    for segment in dot_path.split("."):
        if not isinstance(current, dict):
            raise KeyError(f"expected dict at segment '{segment}', got {type(current).__name__}")
        current = current[segment]
    return current


def _evaluate(actual: Any, op: str, value: Any) -> bool:
    """Evaluate a check. When actual is a list, compare its length."""
    comparable = len(actual) if isinstance(actual, list) else actual
    if op == "gte":
        return comparable >= value
    if op == "eq":
        return comparable == value
    if op == "exists":
        return actual is not None
    raise ValueError(f"unknown op: {op!r}")


def _active_waivers(waivers: list[dict], today: date) -> dict[str, dict]:
    """Return {claim_id: waiver} for waivers that have not yet expired."""
    active: dict[str, dict] = {}
    for w in waivers:
        expires_raw = w.get("expires")
        if expires_raw is None:
            active[w["claim_id"]] = w
        else:
            expires_date = date.fromisoformat(expires_raw)
            if expires_date >= today:
                active[w["claim_id"]] = w
    return active


def run_checks(
    claims_path: Path = CLAIMS_FILE,
    waivers_path: Path = WAIVERS_FILE,
    today: date | None = None,
    repo_root: Path = _REPO_ROOT,
    out=sys.stdout,
) -> int:
    """
    Run all claim checks. Returns exit code (0 = all pass/waived, 1 = any un-waived FAIL).
    Prints a table to `out`.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    claims = _load_json(claims_path)
    waivers_list = _load_json(waivers_path) if waivers_path.exists() else []
    active_waivers = _active_waivers(waivers_list, today)

    col_w = [40, 10, 10, 8]
    header = (
        f"{'claim_id':<{col_w[0]}} {'expected':<{col_w[1]}} {'actual':<{col_w[2]}} {'result':<{col_w[3]}}"
    )
    sep = "-" * sum(col_w + [len(col_w) - 1])
    print(header, file=out)
    print(sep, file=out)

    any_fail = False
    for claim in claims:
        claim_id = claim["claim_id"]
        check = claim["check"]
        knowledge_file = repo_root / check["file"]
        dot_path = check["path"]
        op = check["op"]
        value = check.get("value")

        try:
            data = _load_json(knowledge_file)
            actual = _resolve_path(data, dot_path)
        except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
            actual_display = f"ERROR:{exc}"
            result = "FAIL"
            if claim_id in active_waivers:
                result = "WAIVED"
            else:
                any_fail = True
            _print_row(out, claim_id, _fmt_expected(op, value), actual_display, result, col_w)
            continue

        passed = _evaluate(actual, op, value)
        actual_display = str(len(actual)) if isinstance(actual, list) else str(actual)

        if passed:
            result = "PASS"
        elif claim_id in active_waivers:
            result = "WAIVED"
        else:
            result = "FAIL"
            any_fail = True

        _print_row(out, claim_id, _fmt_expected(op, value), actual_display, result, col_w)

    return 1 if any_fail else 0


def _fmt_expected(op: str, value: Any) -> str:
    if op == "gte":
        return f">={value}"
    if op == "eq":
        return f"=={value}"
    if op == "exists":
        return "exists"
    return str(value)


def _print_row(out, claim_id: str, expected: str, actual: str, result: str, col_w: list[int]) -> None:
    print(
        f"{claim_id:<{col_w[0]}} {expected:<{col_w[1]}} {actual:<{col_w[2]}} {result:<{col_w[3]}}",
        file=out,
    )


if __name__ == "__main__":
    sys.exit(run_checks())
