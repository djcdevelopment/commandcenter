"""Economics summary over the HEARTH ledger.

Pure read, mcp-free. The seed of the knowledge-per-local-hour metric and
the naadam frontier-vs-local scoreboard: per runner_class and per tool,
{calls, ok_rate, total_duration_ms, tokens_in, tokens_out}.

Timestamps are ordered by parsed INSTANT, never by string: hearth-event.v1 admits
several spellings of `ts` that mis-sort lexically (see _parse_ts). This module's
`_parse_ts` is a deliberate twin of the one in hearth.projection.capacity -- a
third copy of the same idea already lives in tools/workflow/corpus_guard.py -- and
all three want one shared home. That hoist is held back only because capacity.py
is under concurrent edit on another branch; once it lands, the three collapse into
one import in a single mechanical move.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from hearth.projection.gemini_pricing import cost_usd

DEFAULT_LEDGER = Path("hearth/var/ledger/events.ndjson")

# Rides offload.v1's real_usd_spent block so a reader knows which price table
# produced the number without opening gemini_pricing.py.
REAL_PRICING_SOURCE = "vertex-ai-pricing verified 2026-07-23 (global/standard, <=200K tier)"

COST_CLASS_MAP = {
    "omen-arc": "sunk",       # resident Qwen3-30B-A3B on OMEN's dual B70s (ADR-0034)
    "omen-arc-oss": "sunk",   # banked-fire gpt-oss-120b, same cards (ADR-0034)
    "omen-ollama": "sunk",
    "omen-swap": "sunk",      # rotation rung -- the SAME dual B70s (ADR-0045); a side
                              # model costs a load, not a dollar
    "fx99-ollama": "sunk",    # RTX 2070 SUPER sidecar on the monitoring node (ADR-0039)
    "am4-oxen": "sunk",       # ☠ tombstone (cards moved to OMEN 2026-08-20)
    "am4-moe": "sunk",        # ☠ tombstone; was resident gpt-oss-120b on AM4 (2026-07-18)
    "gcp-gemini": "trial",
    "gcp-gemini-pro": "trial",
}


def _parse_ts(ts: str) -> datetime | None:
    """Parse a ledger `ts` into an aware UTC datetime; None if it will not parse.

    Timestamps must be ordered by INSTANT, never by string. hearth-event.v1 admits
    three spellings of the same field
    (`^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d+)?(Z|\\+00:00)$`), and 'Z'
    (0x5A) sorts above both '+' (0x2B) and '.' (0x2E), so a lexical max is wrong
    across a mixed-format ledger in two separate ways:

        '2026-07-03T12:00:00Z' > '2026-07-03T12:00:00+00:00'  -> True (same instant)
        '2026-07-03T12:00:00Z' > '2026-07-03T12:00:00.123Z'   -> True (EARLIER wins)

    Comparing parsed instants is immune to the spelling. The ledger happens to be
    uniform today (all 8,540 events carry the fractional-Z form, verified
    2026-07-18), so this is a latent defect, not a live one -- it fires the first
    time any emitter writes `+00:00` or omits fractional seconds, both of which
    the contract permits.

    Never raises: an unparseable ts returns None and is excluded from ordering
    rather than being allowed to win by accident. A naive result (an off-contract
    ts carrying no offset at all) is stamped UTC so every comparison is
    aware-vs-aware and can never raise TypeError on mixed tzinfo.

    Deliberately a twin of hearth.projection.capacity._parse_ts rather than an
    import of it: that module is under concurrent edit on another branch, so the
    two are kept textually interchangeable and hoisted to a shared home in one
    move once both have landed (see the header note).
    """
    try:
        moment = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _empty_bucket() -> dict:
    return {
        "calls": 0,
        "ok": 0,
        "ok_rate": 0.0,
        "total_duration_ms": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }


def _add(bucket: dict, event: dict) -> None:
    bucket["calls"] += 1
    if event.get("ok"):
        bucket["ok"] += 1
    bucket["total_duration_ms"] += int(event.get("duration_ms") or 0)
    cost = event.get("cost") or {}
    bucket["tokens_in"] += int(cost.get("tokens_in") or 0)
    bucket["tokens_out"] += int(cost.get("tokens_out") or 0)


def _finalize(bucket: dict) -> dict:
    bucket["ok_rate"] = round(bucket["ok"] / bucket["calls"], 4) if bucket["calls"] else 0.0
    return bucket


# C-06: per-caller / per-task attribution over the SAME event filter the buckets
# use. An event whose caller.id or task_id is null, missing, blank, or not a
# string is counted under this one key rather than dropped -- a dimension that
# silently discards rows cannot satisfy sum(rows) == totals, and "we do not know
# whose this was" is itself a finding worth reading off the document.
UNSTAMPED_KEY = "(unstamped)"

# Top-N bound for both dimensions. The ledger has an unbounded number of task
# ids (one per session, forever), so an unbounded array would grow without
# limit inside a document every reader loads whole. 50 is also the schema's
# maxItems -- raising it here without raising it there produces a document that
# fails its own contract.
DEFAULT_TOP_N = 50


def _dimension_key(value: object) -> str:
    """The row key for a dimension value: the string itself, or UNSTAMPED_KEY."""
    if isinstance(value, str) and value.strip():
        return value
    return UNSTAMPED_KEY


def _empty_dimension_acc(key: str) -> dict:
    return {
        "key": key,
        "calls": 0,
        "ok": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        # calls per cost class -- the row emits these three counts verbatim.
        "per_class": {"sunk": 0, "trial": 0, "unknown": 0},
        # Offloaded (sunk+trial) tokens: the two inputs the document's
        # est_usd_saved is computed from, kept per row so the row's dollar
        # figure is the SAME arithmetic rather than a second formula. Internal
        # -- the row emits the dollars, not these.
        "offloaded_in": 0,
        "offloaded_out": 0,
        "last_seen": None,
        # Internal ordering key only (see _parse_ts / the buckets loop): never
        # emitted, because a datetime would raise on json.dumps.
        "last_seen_moment": None,
    }


def _add_dimension(acc: dict, cost_class: str, ok: bool, tokens_in: int, tokens_out: int,
                   ts: str | None, moment: datetime | None) -> None:
    acc["calls"] += 1
    if ok:
        acc["ok"] += 1
    acc["tokens_in"] += tokens_in
    acc["tokens_out"] += tokens_out
    acc["per_class"][cost_class] += 1
    if cost_class in ("sunk", "trial"):
        acc["offloaded_in"] += tokens_in
        acc["offloaded_out"] += tokens_out
    if moment is not None and (acc["last_seen_moment"] is None or moment > acc["last_seen_moment"]):
        acc["last_seen_moment"] = moment
        acc["last_seen"] = ts


def _finalize_dimension(data: dict[str, dict], top_n: int) -> tuple[list[dict], bool, int]:
    """(rows, truncated, omitted) for one attribution dimension.

    Ordering is fixed and total: calls descending, then key ascending, so the
    document is a pure function of the ledger bytes even when two keys tie.
    """
    ordered = sorted(data.values(), key=lambda acc: (-acc["calls"], acc["key"]))
    kept = ordered[:top_n]
    omitted = len(ordered) - len(kept)
    rows = []
    for acc in kept:
        usd = (acc["offloaded_in"] * 3.0 + acc["offloaded_out"] * 15.0) / 1_000_000.0
        rows.append({
            "key": acc["key"],
            "calls": acc["calls"],
            "ok_rate": round(acc["ok"] / acc["calls"], 4) if acc["calls"] else 0.0,
            "tokens_in": acc["tokens_in"],
            "tokens_out": acc["tokens_out"],
            "per_class": dict(acc["per_class"]),
            "est_usd_saved_usd": round(usd, 6),
            "last_seen": acc["last_seen"],
        })
    return rows, omitted > 0, omitted


def summarize(ledger_path: Path = DEFAULT_LEDGER) -> dict:
    """Summarize the ledger. Returns
    {per_runner_class, per_tool, frontier_vs_local, events, parse_errors}."""
    per_runner_class: dict[str, dict] = {}
    per_tool: dict[str, dict] = {}
    events = 0
    parse_errors = 0

    if ledger_path.exists():
        for raw_line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue
            events += 1
            runner_class = (event.get("caller") or {}).get("runner_class") or "unknown"
            tool = event.get("tool") or "unknown"
            _add(per_runner_class.setdefault(runner_class, _empty_bucket()), event)
            _add(per_tool.setdefault(tool, _empty_bucket()), event)

    for bucket in per_runner_class.values():
        _finalize(bucket)
    for bucket in per_tool.values():
        _finalize(bucket)

    frontier_vs_local = {
        "frontier": per_runner_class.get("frontier") or _empty_bucket(),
        "local": per_runner_class.get("local") or _empty_bucket(),
    }

    return {
        "per_runner_class": dict(sorted(per_runner_class.items())),
        "per_tool": dict(sorted(per_tool.items())),
        "frontier_vs_local": frontier_vs_local,
        "events": events,
        "parse_errors": parse_errors,
    }


def build_offload_document(ledger_path: Path = DEFAULT_LEDGER, *,
                           top_n: int = DEFAULT_TOP_N) -> dict:
    """Build the offload.v1 document from the ledger (S2, executor-aware).

    Scope: inference-class local_generate events only. Buckets by executor
    backend (S1 provenance field) with a model-name fallback for pre-S1 rows;
    cost classes sunk/trial/unknown make the offload ratio and the
    $-saved-vs-metered-frontier estimate computable.

    `evidence_watermark` is the newest event's ts, and each bucket's `last_seen`
    the newest ts in that bucket -- both chosen by parsed instant rather than by
    string order (see _parse_ts), and both emitted verbatim in the winning event's
    own format, so the document's timestamp spelling is whatever the ledger
    recorded. An event whose ts does not parse cannot win either one.

    Attribution (C-06): `by_caller` and `by_task` answer "what did THIS caller /
    THIS session save?" over the same event filter, with the same arithmetic --
    a row's est_usd_saved_usd is the document's own est_usd_saved formula
    (3.0/15.0 per Mtok of reference frontier price) applied to that row's
    offloaded (sunk+trial) tokens. Untruncated, sum(rows.calls) == totals.calls
    and the token sums match too, because a row is never dropped: an event with
    no caller.id or no task_id lands under the single key "(unstamped)".

    PRIVATE DOCUMENT. `by_caller[].key` is a `caller.id` -- an internal identity
    from callers.json, not a public handle -- and `by_task[].key` is a caller-
    chosen task/session id. This document is written to `knowledge/offload.json`,
    which is private; nothing here is published. The public portfolio projection
    (`hearth/projection/public_portfolio.py`) has its own explicit key allowlist
    and does not read these keys. Counts, ids and timestamps only: no prompt
    text, no arguments, no paths, no error strings ever enter this document.

    `top_n` bounds each dimension (default 50, the schema's maxItems). What is
    cut is stated, not silently dropped: `by_caller_truncated`/`by_task_truncated`
    and `by_caller_omitted`/`by_task_omitted` say so on the document itself.
    """
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1:
        raise ValueError("top_n must be a positive integer")
    totals = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
    per_class = {
        "sunk": {"calls": 0, "tokens_in": 0, "tokens_out": 0},
        "trial": {"calls": 0, "tokens_in": 0, "tokens_out": 0},
        "unknown": {"calls": 0, "tokens_in": 0, "tokens_out": 0},
    }
    # Real-dollar accounting over TRIAL calls only: sunk is $0 by definition and
    # unknown is unpriceable. unpriced_calls counts trial calls cost_usd could
    # not price (unlisted model, or null token counts -- the legacy zero-token
    # buckets land here), so the usd figure is honestly a floor, not a total.
    real = {"usd": 0.0, "priced_calls": 0, "unpriced_calls": 0}
    buckets_data = {}
    by_caller_data: dict[str, dict] = {}
    by_task_data: dict[str, dict] = {}
    newest_ts: str | None = None
    newest_moment: datetime | None = None
    line_count = 0

    if ledger_path.exists():
        for raw_line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            line_count += 1
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue

            if event.get("task_class") != "inference" or event.get("tool") != "local_generate":
                continue

            backend = event.get("backend")
            model = event.get("model") or ""

            if backend:
                cost_class = COST_CLASS_MAP.get(backend, "unknown")
                bucket_key = backend
            else:
                lower_model = model.lower()
                if "gemini" in lower_model:
                    cost_class = "trial"
                elif "qwen" in lower_model or "oxen" in lower_model or "gguf" in lower_model:
                    cost_class = "sunk"
                else:
                    cost_class = "unknown"
                bucket_key = f"model:{model}"

            acc = buckets_data.setdefault(bucket_key, {
                "backend": bucket_key,
                "cost_class": cost_class,
                "models": set(),
                "calls": 0,
                "ok": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "real_usd": None,
                "last_seen": None,
                # Internal ordering key only -- the emit loop below builds each
                # bucket field-by-field, so this never reaches the document.
                "last_seen_moment": None,
            })

            if model:
                acc["models"].add(model)
            acc["calls"] += 1
            if event.get("ok"):
                acc["ok"] += 1

            cost = event.get("cost") or {}
            tokens_in = int(cost.get("tokens_in") or 0)
            tokens_out = int(cost.get("tokens_out") or 0)

            acc["tokens_in"] += tokens_in
            acc["tokens_out"] += tokens_out

            totals["calls"] += 1
            totals["tokens_in"] += tokens_in
            totals["tokens_out"] += tokens_out

            per_class[cost_class]["calls"] += 1
            per_class[cost_class]["tokens_in"] += tokens_in
            per_class[cost_class]["tokens_out"] += tokens_out

            if cost_class == "trial":
                # Raw (possibly-null) token counts on purpose: cost_usd returns
                # None for an unpriceable call, and that must count as unpriced
                # rather than silently pricing null as zero tokens.
                call_usd = cost_usd(backend, model or None,
                                    cost.get("tokens_in"), cost.get("tokens_out"))
                if call_usd is None:
                    real["unpriced_calls"] += 1
                else:
                    real["usd"] += call_usd
                    real["priced_calls"] += 1
                    acc["real_usd"] = (acc["real_usd"] or 0.0) + call_usd

            ts = event.get("ts")
            moment = _parse_ts(ts) if isinstance(ts, str) else None
            if moment is not None:
                # Order by parsed instant, emit the original string (see _parse_ts).
                if newest_moment is None or moment > newest_moment:
                    newest_moment = moment
                    newest_ts = ts
                if acc["last_seen_moment"] is None or moment > acc["last_seen_moment"]:
                    acc["last_seen_moment"] = moment
                    acc["last_seen"] = ts

            # C-06: the same event, attributed two more ways. Same filter, same
            # cost class, same token counts -- so the dimensions cannot disagree
            # with the totals they are read beside.
            ok = bool(event.get("ok"))
            caller_key = _dimension_key((event.get("caller") or {}).get("id")
                                        if isinstance(event.get("caller"), dict) else None)
            task_key = _dimension_key(event.get("task_id"))
            _add_dimension(
                by_caller_data.setdefault(caller_key, _empty_dimension_acc(caller_key)),
                cost_class, ok, tokens_in, tokens_out, ts if moment is not None else None, moment)
            _add_dimension(
                by_task_data.setdefault(task_key, _empty_dimension_acc(task_key)),
                cost_class, ok, tokens_in, tokens_out, ts if moment is not None else None, moment)

    buckets = []
    for bk, acc in buckets_data.items():
        # real_usd semantics: sunk -> 0.0 (known free), trial -> priced sum or
        # None when nothing in the bucket was priceable, unknown -> None.
        if acc["cost_class"] == "sunk":
            bucket_real_usd = 0.0
        elif acc["real_usd"] is not None:
            bucket_real_usd = round(acc["real_usd"], 6)
        else:
            bucket_real_usd = None
        buckets.append({
            "backend": acc["backend"],
            "cost_class": acc["cost_class"],
            "models": sorted(list(acc["models"])),
            "calls": acc["calls"],
            "ok_rate": round(acc["ok"] / acc["calls"], 4) if acc["calls"] else 0.0,
            "tokens_in": acc["tokens_in"],
            "tokens_out": acc["tokens_out"],
            "real_usd": bucket_real_usd,
            "last_seen": acc["last_seen"],
        })

    buckets.sort(key=lambda b: (b["backend"] or "", b["cost_class"]))

    offloaded_out = per_class["sunk"]["tokens_out"] + per_class["trial"]["tokens_out"]
    offload_ratio = round(offloaded_out / totals["tokens_out"], 4) if totals["tokens_out"] else None

    offloaded_in = per_class["sunk"]["tokens_in"] + per_class["trial"]["tokens_in"]
    usd = (offloaded_in * 3.0 + offloaded_out * 15.0) / 1_000_000.0

    by_caller, by_caller_truncated, by_caller_omitted = _finalize_dimension(by_caller_data, top_n)
    by_task, by_task_truncated, by_task_omitted = _finalize_dimension(by_task_data, top_n)

    # Same content-shaped digest scheme capacity.json uses: changes iff the
    # ledger's non-blank line count changes, stable across checkouts.
    corpus_digest = f"sha256:{hashlib.sha256(f'events.ndjson:{line_count}'.encode('utf-8')).hexdigest()}"

    return {
        "contract_version": "offload.v1",
        "evidence_watermark": newest_ts,
        "bucket_count": len(buckets),
        "totals": totals,
        "per_class": per_class,
        "offload_ratio": offload_ratio,
        "est_usd_saved": {
            "reference": "claude-sonnet",
            "input_per_mtok": 3.0,
            "output_per_mtok": 15.0,
            "usd": round(usd, 6)
        },
        "real_usd_spent": {
            "pricing_source": REAL_PRICING_SOURCE,
            "usd": round(real["usd"], 6),
            "priced_calls": real["priced_calls"],
            "unpriced_calls": real["unpriced_calls"],
        },
        "buckets": buckets,
        # Attribution dimensions (C-06). Private identifiers; see the docstring.
        "by_caller": by_caller,
        "by_caller_truncated": by_caller_truncated,
        "by_caller_omitted": by_caller_omitted,
        "by_task": by_task,
        "by_task_truncated": by_task_truncated,
        "by_task_omitted": by_task_omitted,
        "corpus_digest": corpus_digest,
        "corpus_event_count": line_count,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.projection.economics",
        description="Summarize HEARTH ledger economics (pure read).",
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args(argv[1:])

    print(json.dumps(summarize(args.ledger), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
