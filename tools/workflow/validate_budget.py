from __future__ import annotations

import json
import sys
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "operating-budget.v1.schema.json"

_HH_MM_PATTERN_CHARS = set("0123456789:")


class ValidationError(Exception):
    pass


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _check_hhmm(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    parts = value.split(":")
    if len(parts) != 2:
        raise ValidationError(f"{field} must be HH:MM")
    hh, mm = parts
    if not (hh.isdigit() and mm.isdigit()):
        raise ValidationError(f"{field} must be HH:MM with digits")
    if not (0 <= int(hh) <= 23):
        raise ValidationError(f"{field} hour must be 00-23")
    if not (0 <= int(mm) <= 59):
        raise ValidationError(f"{field} minute must be 00-59")


def validate_budget(budget: dict) -> None:
    schema = _load_schema()
    required = schema["required"]
    missing = sorted(name for name in required if name not in budget)
    if missing:
        raise ValidationError(f"missing required fields: {', '.join(missing)}")

    allowed_keys = set(schema["properties"].keys())
    extra = sorted(k for k in budget if k not in allowed_keys)
    if extra:
        raise ValidationError(f"additional properties not allowed: {', '.join(extra)}")

    if budget.get("contract_version") != "operating-budget.v1":
        raise ValidationError("contract_version must be 'operating-budget.v1'")

    if not isinstance(budget.get("budget_id"), str) or not budget["budget_id"]:
        raise ValidationError("budget_id must be a non-empty string")

    node_id = budget.get("node_id")
    if node_id is not None and not isinstance(node_id, str):
        raise ValidationError("node_id must be string or null")

    for num_field in ("max_gpu_temp_c", "max_power_w", "max_fan_rpm"):
        val = budget.get(num_field)
        if val is not None and not isinstance(val, (int, float)):
            raise ValidationError(f"{num_field} must be number or null")

    if not isinstance(budget.get("unattended_dispatch_allowed"), bool):
        raise ValidationError("unattended_dispatch_allowed must be a boolean")

    active_hours = budget.get("active_hours")
    if active_hours is not None:
        if not isinstance(active_hours, dict):
            raise ValidationError("active_hours must be an object or null")
        for sub in ("start", "end"):
            if sub not in active_hours:
                raise ValidationError(f"active_hours.{sub} is required")
        _check_hhmm(active_hours["start"], "active_hours.start")
        _check_hhmm(active_hours["end"], "active_hours.end")
        extra_sub = sorted(k for k in active_hours if k not in ("start", "end"))
        if extra_sub:
            raise ValidationError(f"active_hours has additional properties: {', '.join(extra_sub)}")

    reason = budget.get("reason")
    if not isinstance(reason, str) or not reason:
        raise ValidationError("reason must be a non-empty string")

    authored_by = budget.get("authored_by")
    if not isinstance(authored_by, str) or not authored_by:
        raise ValidationError("authored_by must be a non-empty string")

    if not isinstance(budget.get("suspended"), bool):
        raise ValidationError("suspended must be a boolean")

    suspend_reason = budget.get("suspend_reason")
    if suspend_reason is not None and not isinstance(suspend_reason, str):
        raise ValidationError("suspend_reason must be string or null")


def validate_file(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        budget = json.loads(path.read_text(encoding="utf-8"))
        validate_budget(budget)
    except json.JSONDecodeError as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
    except ValidationError as exc:
        errors.append(f"{path}: {exc}")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m tools.workflow.validate_budget <budget.json> [more files]")
        return 2

    all_errors: list[str] = []
    for raw_path in argv[1:]:
        all_errors.extend(validate_file(Path(raw_path)))

    if all_errors:
        for error in all_errors:
            print(error)
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
