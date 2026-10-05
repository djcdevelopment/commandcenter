"""Which delivery procedure the door runs for a backend and task family, read from delivery-procedures.v1."""
from __future__ import annotations

import hashlib
import json
import os

SCHEMA = "delivery-procedures.v1"
KNOWN = ("carry", "one_call")
COUNT_KEYS = ("accepted", "rejected", "briefs", "accepted_briefs")


class ProcedureTableError(ValueError):
    pass


def load(path: str | os.PathLike | None) -> tuple[dict | None, str | None]:
    if not path or not os.path.exists(path):
        return None, None
    try:
        with open(path, "rb") as source:
            data = source.read()
        table = json.loads(data)
    except (OSError, ValueError) as exc:
        raise ProcedureTableError(f"procedure table {path} is not readable JSON: {exc}") from exc
    rule = table.get("rule") if isinstance(table, dict) else None
    if (not isinstance(table, dict) or table.get("schema") != SCHEMA or not isinstance(table.get("backends"), dict)
            or not isinstance(rule, dict) or isinstance(rule.get("min_accepted_briefs"), bool)
            or not isinstance(rule.get("min_accepted_briefs"), int) or rule["min_accepted_briefs"] < 1):
        raise ProcedureTableError(f"procedure table {path} is not a {SCHEMA} table")
    return table, hashlib.sha256(data).hexdigest()


def _counts(level: object, where: str) -> dict:
    if not isinstance(level, dict):
        raise ProcedureTableError(f"procedure table {where} must be an object")
    for name, entry in level.items():
        if name in KNOWN and not (isinstance(entry, dict) and all(
                isinstance(entry.get(k), int) and not isinstance(entry.get(k), bool) for k in COUNT_KEYS)):
            raise ProcedureTableError(f"procedure table {where}.{name} lacks integer {COUNT_KEYS}")
    return level


def _pick(level: dict, minimum: int) -> tuple[str | None, dict]:
    counts = {n: {k: e[k] for k in COUNT_KEYS} for n, e in level.items() if n in KNOWN}
    ok = [n for n, c in counts.items() if c["accepted_briefs"] >= minimum]
    if not ok:
        return None, counts
    # higher accepted share, then more accepted, then one_call
    best = max(ok, key=lambda n: (counts[n]["accepted"] / max(1, counts[n]["accepted"] + counts[n]["rejected"]),
                                  counts[n]["accepted"], n == "one_call"))
    return best, counts


def choose(table: dict | None, backend: str, task_family: str | None, *,
           serving_profile_sha256: str | None = None, require_profile: bool = False) -> tuple[str, dict]:
    if table is None:
        return "one_call", {"level": "none", "rule": None, "counts": {}}
    rule = table["rule"]
    entry = table["backends"].get(backend)
    if entry is None:
        return "one_call", {"level": "none", "rule": rule, "counts": {}}
    if not isinstance(entry, dict) or not isinstance(entry.get("families") or {}, dict):
        raise ProcedureTableError(f"procedure table {backend} must be an object with an object of families")
    if "profiles" in entry and not isinstance(entry["profiles"], dict):
        raise ProcedureTableError(f"procedure table {backend}.profiles must be an object")
    if require_profile or (serving_profile_sha256 is not None and entry.get("profiles")):
        profiles = entry.get("profiles", {})
        if not isinstance(profiles, dict):
            raise ProcedureTableError(f"procedure table {backend}.profiles must be an object")
        entry = profiles.get(serving_profile_sha256)
        if entry is None:
            return "one_call", {"level": "none", "rule": rule, "counts": {},
                                "unqualified_profile": serving_profile_sha256}
        if not isinstance(entry, dict) or not isinstance(entry.get("families") or {}, dict):
            raise ProcedureTableError(f"procedure table {backend} profile must contain object counts")
    minimum = rule["min_accepted_briefs"]
    levels = []
    if task_family is not None and task_family in (entry.get("families") or {}):
        levels.append(("family", entry["families"][task_family]))
    levels.append(("backend", entry.get("all") or {}))
    for name, level in levels:
        ignored = sorted(n for n in _counts(level, f"{backend}.{name}") if n not in KNOWN)
        best, counts = _pick(level, minimum)
        if best:
            basis = {"level": name, "rule": rule, "counts": counts}
            if ignored:
                basis["ignored"] = ignored
            return best, basis
    return "one_call", {"level": "none", "rule": rule, "counts": {}}
