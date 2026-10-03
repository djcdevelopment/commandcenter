PROOFING_TASK_CLASS = backlog_sources.CANDIDATE_TASK_CLASS   # "proofing": a priced candidate
_CANDIDATE_ID_RE = re.compile(r"Run experiment candidate '([^']+)'")   # candidate_prompt is byte-pinned
SKIP_DAYS = 7

def load_skips(path: Optional[Path] = None) -> list[dict[str, Any]]:
    p = path or skips_path()
    try:
        return list(json.loads(p.read_text()).get("skips") or [])
    except (OSError, ValueError):
        return []


def add_skip(source_ref: str, reason: str, *, days: int = SKIP_DAYS, path: Optional[Path] = None,
             now: Optional[datetime] = None) -> None:
    p = path or skips_path(); now = now or datetime.utcnow()
    rows = load_skips(p)
    rows.append({"source": "candidate", "source_ref": source_ref, "reason": reason[:200],
                 "at": now.isoformat(timespec="seconds") + "Z",
                 "until": (now + timedelta(days=days)).isoformat(timespec="seconds") + "Z"})
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps({"schema": "bankedfire-linux-skips.v1", "skips": rows}, indent=2)); os.replace(tmp, p)


def candidate_exclusions(now: Optional[datetime] = None, *, skips: Optional[Path] = None,
                         worth_path: Optional[Path] = None, candidates_path: Optional[Path] = None) -> frozenset:
    """Unexpired skips plus priced ids that no longer exist in the derived candidate list.
    Stale ids are computed, never written: a rebuild that resurrects an id un-stales it."""
    now = now or datetime.utcnow(); stamp = now.isoformat(timespec="seconds") + "Z"
    out = {r["source_ref"] for r in load_skips(skips) if r.get("source") == "candidate" and str(r.get("until", "")) > stamp}
    try:
        worth = json.loads(Path(worth_path or backlog_sources.DEFAULT_CANDIDATE_WORTH_PATH).read_text()).get("entries") or []
        known = {c.get("candidate_id") for c in json.loads(Path(candidates_path or backlog_sources.DEFAULT_EXPERIMENT_CANDIDATES_PATH).read_text()).get("candidates") or []}
    except (OSError, ValueError):
        return frozenset(out)
    out |= {e["candidate_id"] for e in worth if isinstance(e, dict) and isinstance(e.get("candidate_id"), str)
            and e.get("status") != backlog_sources.CANDIDATE_RETIRED and e["candidate_id"] not in known}
    return frozenset(out)


def submit_task(**kwargs: Any) -> dict[str, Any]:
    """The drain's submit hook. kwargs come from Brief.submit_kwargs() + prompt=body."""
    body = str(kwargs.get("prompt") or "")
    hint = str(kwargs.get("plan_id_hint") or "brief")
    if kwargs.get("task_class") == EXPERIMENT_TASK_CLASS:
        ran_drain += 1
        report["reason"] = result["reason"]
        if not str(result["reason"]).startswith("dispatched:"):
            detail = result.get("detail") or {}
            if result["reason"] == "no-op:dispatch-failed" and detail.get("source") == "candidate" and detail.get("source_ref"):
                # a candidate that cannot be dispatched must not be re-picked every 30 minutes
                add_skip(str(detail["source_ref"]), f"dispatch-failed: {detail.get('submit_error')}")
                report["skipped"].append({"source_ref": detail["source_ref"], "days": SKIP_DAYS})
            break
