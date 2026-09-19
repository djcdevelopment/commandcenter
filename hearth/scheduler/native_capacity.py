"""Passive AM4 serve truth. Readiness is not a GPU-placement certificate."""
from datetime import datetime, timezone

ALIAS = "am4-dense-27b"
RESOURCE = "am4:127.0.0.1:18090"
MODEL = "Qwen3.8-27B-Q4_K_M.gguf"


def normalize_am4_native(payload, observed_at, now=None):
    result = {"observed_at": observed_at, "ready": False, "parallel_slots": 0,
              "gpu_placed": None, "gpu_qualified_models": [], "loaded_models": [],
              "reason": "invalid or unavailable native response"}
    try:
        stamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return result
        age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
        if not -5 <= age <= 30:
            return {**result, "reason": "stale or future native observation"}
        aliases = payload.get("aliases")
        if not isinstance(aliases, list):
            return result
        rows = [row for row in aliases if isinstance(row, dict) and row.get("alias") == ALIAS]
        if not rows or any(row != rows[0] for row in rows[1:]):
            return result
        row = rows[0]
        if (row.get("ready") is not True or type(row.get("status")) is not int or row["status"] != 200
                or type(row.get("context_length")) is not int or row["context_length"] != 131072
                or type(row.get("parallel_slots")) is not int or row["parallel_slots"] != 1
                or row.get("physical_resource") != RESOURCE
                or not isinstance(row.get("model"), str) or row["model"].rsplit("/",1)[-1] != MODEL):
            return result
        return {**result, "ready": True, "parallel_slots": 1,
                "physical_resource": RESOURCE, "context_length": row["context_length"],
                "model": row["model"], "loaded_models": [MODEL],
                "reason": "native endpoint ready; full GPU placement independently unverified"}
    except (AttributeError, TypeError, ValueError, OverflowError):
        return result
