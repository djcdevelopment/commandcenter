"""Qualify Wan2.1 serially through the live HEARTH gateway.

The gateway remains the sole execution-ledger writer. Each animation call owns
its image-session transition, verifies the fixed H.264 clip, and restores
ArcServe before this script advances to the next lane.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hearth.callers.client import DEFAULT_ENDPOINT, HearthClient

LANES = ("b70@bus4", "b70@bus9")
TERMINAL = {"succeeded", "failed", "cancelled", "rejected", "expired"}


def _payload(result: dict) -> dict:
    if not result.get("ok"):
        raise RuntimeError(result.get("text") or "HEARTH MCP call failed")
    structured = result.get("structured")
    if isinstance(structured, dict):
        return structured
    try:
        value = json.loads(result.get("text") or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("HEARTH returned a non-JSON tool result") from exc
    if not isinstance(value, dict):
        raise RuntimeError("HEARTH returned an invalid tool result")
    if value.get("ok") is False:
        raise RuntimeError(str(value.get("error") or value))
    return value


def _caller_key(caller_id: str, callers_path: Path) -> str:
    explicit = os.environ.get("HEARTH_KEY")
    if explicit:
        return explicit
    callers = json.loads(callers_path.read_text(encoding="utf-8"))
    return next(
        key for key, value in callers.items()
        if isinstance(value, dict) and value.get("id") == caller_id
    )


def _wait_job(client: HearthClient, job_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    state: dict = {}
    while time.monotonic() < deadline:
        state = _payload(client.call_sync("get_media_status", job_id=job_id))
        if state.get("status") in TERMINAL:
            return state
        time.sleep(5)
    client.call_sync(
        "cancel_media", job_id=job_id,
        reason="Wan qualification exceeded its deadline",
    )
    raise TimeoutError("Wan qualification job exceeded timeout: " + job_id)


def _wait_llm(client: HearthClient, timeout: float = 10 * 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _payload(client.call_sync("get_image_session"))
        session = state.get("session") or {}
        if session.get("state") == "llm" and not session.get("active"):
            return state
        if session.get("state") == "faulted":
            raise RuntimeError("tenancy transition faulted: " + str(session.get("reason")))
        time.sleep(3)
    raise TimeoutError("ArcServe was not restored after Wan qualification")


def qualify(client: HearthClient, input_path: Path, lanes: tuple[str, ...],
            timeout: float) -> dict:
    results: list[dict] = []
    run_id = str(time.time_ns())
    initial = _payload(client.call_sync("get_image_session"))
    session = initial.get("session") or {}
    if session.get("state") != "llm" or session.get("active"):
        raise RuntimeError("qualification requires a clean ArcServe/llm baseline")

    for lane in lanes:
        row: dict = {"lane": lane, "ok": False}
        try:
            submitted = _payload(client.call_sync(
                "submit_video_animation",
                still_image_path=str(input_path),
                motion_prompt="A subtle slow camera push, natural motion, stable scene",
                target_lane=lane,
                deadline_s=int(timeout),
                idempotency_key=(
                    "wan-qualification-" + run_id + "-" + lane.replace("@", "-")
                ),
            ))
            row["job_id"] = submitted["job_id"]
            final = _wait_job(client, submitted["job_id"], timeout + 30)
            row.update(status=final.get("status"), reason=final.get("reason"),
                       progress=final.get("progress") or {})
            if final.get("status") != "succeeded":
                raise RuntimeError(str(final.get("reason") or "animation job failed"))
            row["result_artifact_id"] = final.get("result_artifact_id")
            row["ok"] = True
        except Exception as exc:
            row["error"] = str(exc)
        finally:
            try:
                _wait_llm(client)
            except Exception as exc:
                row["ok"] = False
                row["restore_error"] = str(exc)
        results.append(row)

    return {
        "ok": any(row["ok"] for row in results),
        "results": results,
        "qualified_lanes": [row["lane"] for row in results if row["ok"]],
        "quarantined_lanes": [row["lane"] for row in results if not row["ok"]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--lane", action="append", choices=LANES)
    parser.add_argument("--timeout-s", type=int, default=7200)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--caller-id", default="claude-frontier")
    parser.add_argument("--callers", type=Path, default=Path("hearth/var/callers.json"))
    args = parser.parse_args(argv)
    input_path = args.input.resolve(strict=True)
    key = _caller_key(args.caller_id, args.callers.resolve(strict=True))
    value = qualify(
        HearthClient(endpoint=args.endpoint, key=key), input_path,
        tuple(args.lane or LANES), float(args.timeout_s),
    )
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0 if value["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
