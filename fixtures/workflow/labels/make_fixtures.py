"""Generate the synthetic labels fixtures (Track C). Deterministic; re-run to regenerate.

    python3 fixtures/workflow/labels/make_fixtures.py

Writes, beside this file:
    runs/work_<id>/{refs/envelope.json, work-manifest.json, history.ndjson[, delivery.json]}   4 runs
    ledger/events.ndjson + ledger/index.sqlite        HEARTH-ledger shaped rows (get_local_work_artifact and decoys)
    transcripts/-fixture-project/<sid>.jsonl          two sessions, metadata-shaped records only (no text)
    corpus/runs.jsonl                                 precursor-run.v2 rows carrying group_id for two runs

Runs (all timestamps UTC, 2026-10-16 unless noted):
    R1 work_a1a1...  Track-B style: step.dispatched with the new keys, artifact.fetched, verdict events with
                     bucket/attended/task_id/validator, criteria rows {criterion, status, evidence},
                     evidence `review_minutes_active=6`, delivery.json. accepted.
                     produced 10:02, fetched 10:10 (queue 8.0), verdict 10:30 (elapsed 20.0, submit->accept 30.0)
    R2 work_b2b2...  historic (2026-10-01): manifest-only verdict (no verification/outcome events), submitted
                     by bankedfire-drain, ledger first fetch 07:20 by claude-frontier (queue 15.0), accepted.
    R3 work_c3c3...  rejected without bucket (old-style events), ledger first fetch 08:13 by codex-cli (queue
                     10.0), verdict 08:33 (elapsed 20.0); a second, later ledger fetch must not win.
    R4 work_d4d4...  accepted, old-style events, summary "Review minutes: 3", no fetch anywhere; same lineage
                     group as R2 in the corpus (rework = true).

Transcript session A (sid aaaaaaaa-...): reviews R1 and R4 with overlapping spans and a Monitor-tick burst
(records at 10:20, 10:21, 10:22, 10:23, 10:24 -> four 60 s gaps that add no active time).
    R1 span [10:04:30, 10:30:00]: raw active 370 s, concurrent 2 -> 3.08 min
    R4 span [10:05:00, 10:40:00]: raw active 430 s, concurrent 2 -> 3.58 min
Session B (sid bbbbbbbb-...): reviews R3 alone, 11:00:00 -> 11:01:00, raw active 60 s, concurrent 1 -> 1.0 min.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = "work_" + "a1" * 16
R2 = "work_" + "b2" * 16
R3 = "work_" + "c3" * 16
R4 = "work_" + "d4" * 16
OTHER = "work_" + "e5" * 16
SID_A = "aaaaaaaa-1111-4111-8111-111111111111"
SID_B = "bbbbbbbb-2222-4222-8222-222222222222"
ENV_ID = "e" * 64
PROFILE_SHA = "ab" * 32
ROUTE_SHA = "cd" * 32
ART_SHA = "0f" * 32


def event(run_id: str, kind: str, ts: str, payload: dict, seq: int) -> dict:
    return {"contract_version": "operator-history-event.v1", "envelope_id": ENV_ID,
            "event_id": f"{run_id}-{seq:02d}", "event_type": kind, "payload": payload, "refs": {},
            "run_id": run_id, "sequence": seq, "timestamp": ts}


def write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_ndjson(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def envelope(intent: str, submitted_by: str, submitted_at: str, criteria: list[str]) -> dict:
    return {"contract_version": "task-envelope.v1", "envelope_id": ENV_ID, "intent": intent,
            "acceptance_criteria": criteria, "submitted_by": submitted_by, "submitted_at": submitted_at,
            "classification": "code", "constraints": {}, "inputs": {}, "submission_source": "mcp", "supersedes": None}


def route(backend: str, family: str, procedure: str = "one_call", tokens=(3000, 900, 15000)) -> dict:
    return {"backend": backend, "provider": backend, "model": "qwen3.8-27b", "selected_model": "qwen3.8-27b",
            "task_family": family, "requested_lane": "auto", "selected_lane": "deep", "procedure": procedure,
            "profile_sha256": ROUTE_SHA, "profile_version": "local-work-routes.v2",
            "serving_profile_sha256": PROFILE_SHA, "serving_profile_schema": "serving-profile.v2",
            "routed_by": "lane:deep", "tokens_in": tokens[0], "tokens_out": tokens[1], "duration_ms": tokens[2]}


def conditions(temp=None) -> dict:
    return {"environment": "dev", "lab_configuration": "three-dense", "lab_configuration_source": "profiles",
            "temperature": temp}


def make_runs(root: Path) -> None:
    # ---- R1: Track-B style, accepted
    sub = "2026-10-16T10:00:00Z"
    d = root / R1
    write_json(d / "refs" / "envelope.json", envelope("add a --json flag to ct stats", "claude-frontier", sub,
                                                       ["prints one JSON object", "existing text output unchanged"]))
    events = [
        event(R1, "task.received", sub, {"criteria_count": 2, "envelope_id": ENV_ID, "intent_chars": 30,
                                        "intent_sha256": "1" * 64, "submitted_by": "claude-frontier"}, 1),
        event(R1, "step.dispatched", "2026-10-16T10:00:05Z", {
            "attempt": 1, "job_id": "job_r1", "model": "qwen3.8-27b", "provider": "omen-dense-27b",
            "task_family": "code_fix", "lane": "auto", "selected_lane": "deep", "serving_profile_sha256": PROFILE_SHA,
            "route_profile_sha256": ROUTE_SHA, "procedure": "one_call", "lab_configuration": "three-dense",
            "temperature": 0.0}, 2),
        event(R1, "attempt.recorded", "2026-10-16T10:02:00Z", {"job_id": "job_r1", "ok": True}, 3),
        event(R1, "artifact.produced", "2026-10-16T10:02:00Z", {"artifact_id": "art_r1", "sha256": ART_SHA, "size": 1234,
                                                                "delivery": True}, 4),
        event(R1, "verification.recorded", "2026-10-16T10:02:01Z", {"checks": ["schema", "git_apply_check"], "passed": True}, 5),
        event(R1, "artifact.fetched", "2026-10-16T10:10:00Z", {"by": "claude-frontier", "status": "awaiting_review"}, 6),
        event(R1, "verification.recorded", "2026-10-16T10:30:00Z", {
            "criteria_count": 2, "decision": "accepted", "evidence_count": 2, "passed": True, "validator": "claude-frontier",
            "bucket": "form", "attended": True, "task_id": "cc-aaaaaaaa"}, 7),
        event(R1, "outcome.final", "2026-10-16T10:30:00Z", {"status": "accepted", "validator": "claude-frontier",
                                                            "bucket": "form", "attended": True, "task_id": "cc-aaaaaaaa"}, 8),
    ]
    write_ndjson(d / "history.ndjson", events)
    write_json(d / "work-manifest.json", {
        "schema": "work-manifest.v1", "work_id": R1, "status": "accepted", "envelope_id": ENV_ID,
        "caller": {"submitted_by": "claude-frontier", "validated_by": "claude-frontier"},
        "conditions": conditions(0.0), "route": route("omen-dense-27b", "code_fix"),
        "attempts": [{"attempt": 1, "job_id": "job_r1", "ok": True, "structured_output_repairs": ["control_char_in_string"]}],
        "artifact": {"artifact_id": "art_r1", "sha256": ART_SHA, "size": 1234},
        "verdict": {"decision": "accepted", "bucket": "form", "caller_id": "claude-frontier", "receipt_id": None,
                    "summary": "one call, compiled first try; one quote repaired (form).",
                    "evidence": ["review_minutes_active=6", "tests/test_ct.py: 3 passed"],
                    "criteria": [{"criterion": "prints one JSON object", "status": "passed",
                                  "evidence": "python ct.py stats --json | jq . ok"},
                                 {"criterion": "existing text output unchanged", "status": "passed",
                                  "evidence": "diff of text output empty"}]},
    })
    write_json(d / "delivery.json", {
        "schema": "delivery.v1", "backend": "omen-dense-27b", "model": "qwen3.8-27b", "procedure": "one_call",
        "brief_sha256": "2" * 64, "environment": "dev", "aids_used": ["source_map", "quote_renderer"],
        "repairs": {"ambiguous_quote": 1}, "suspected_incomplete": False, "unsupported": ["s0.p0.q2"],
        "claims": [{"id": "s0.p0.q0", "match": "exact", "resolved": {"path": "ct/ct.py", "line": 10}},
                   {"id": "s0.p0.q1", "match": "exact", "resolved": {"path": "ct/ct.py", "line": 12}},
                   {"id": "s0.p0.q2", "match": "missing", "resolved": None}],
        "verification": {"deterministic": {"state": "pass"}, "human": {"state": "not_run"}},
        "deviations": [], "items": [], "measures": {"words": 120},
    })

    # ---- R2: historic, manifest-only verdict, unattended submission, ledger fetch
    sub = "2026-10-01T07:00:00Z"
    d = root / R2
    write_json(d / "refs" / "envelope.json", envelope("night brief: streams row contract report", "bankedfire-drain", sub,
                                                       ["states every REQUIRED field"]))
    events = [
        event(R2, "task.received", sub, {"criteria_count": 1, "envelope_id": ENV_ID, "intent_chars": 40,
                                        "intent_sha256": "3" * 64, "submitted_by": "bankedfire-drain"}, 1),
        event(R2, "step.dispatched", "2026-10-01T07:00:10Z", {"attempt": 1, "job_id": "job_r2", "model": "qwen3.8-27b",
                                                              "provider": "omen-dense-27b-b"}, 2),
        event(R2, "attempt.recorded", "2026-10-01T07:05:00Z", {"job_id": "job_r2", "ok": True}, 3),
        event(R2, "artifact.produced", "2026-10-01T07:05:00Z", {"artifact_id": "art_r2", "sha256": ART_SHA, "size": 2222}, 4),
    ]
    write_ndjson(d / "history.ndjson", events)
    write_json(d / "work-manifest.json", {
        "schema": "work-manifest.v1", "work_id": R2, "status": "accepted", "envelope_id": ENV_ID,
        "caller": {"submitted_by": "bankedfire-drain", "validated_by": "claude-frontier"},
        "conditions": conditions(None), "route": route("omen-dense-27b-b", "quote_retrieval", tokens=(2500, 700, 14000)),
        "attempts": [{"attempt": 1, "job_id": "job_r2", "ok": True}],
        "verdict": {"decision": "accepted", "caller_id": "claude-frontier", "receipt_id": None,
                    "summary": "substance holds on the one criterion.", "evidence": ["streams/lib/receipt.py@aa85a801"],
                    "criteria": [{"id": "c1", "state": "passed", "note": "REQUIRED tuple listed"}]},
    })

    # ---- R3: rejected without bucket, old-style events, ledger fetch by codex-cli
    sub = "2026-10-16T08:00:00Z"
    d = root / R3
    write_json(d / "refs" / "envelope.json", envelope("fix the off-by-one in waits panel", "claude-frontier", sub,
                                                       ["panel shows the cleared count"]))
    events = [
        event(R3, "task.received", sub, {"criteria_count": 1, "envelope_id": ENV_ID, "intent_chars": 33,
                                        "intent_sha256": "4" * 64, "submitted_by": "claude-frontier"}, 1),
        event(R3, "step.dispatched", "2026-10-16T08:00:05Z", {"attempt": 1, "job_id": "job_r3", "model": "qwen3.8-27b",
                                                              "provider": "am4-vllm"}, 2),
        event(R3, "attempt.recorded", "2026-10-16T08:03:00Z", {"job_id": "job_r3", "ok": True}, 3),
        event(R3, "artifact.produced", "2026-10-16T08:03:00Z", {"artifact_id": "art_r3", "sha256": ART_SHA, "size": 999}, 4),
        event(R3, "verification.recorded", "2026-10-16T08:33:00Z", {"criteria_count": 1, "decision": "rejected",
                                                                    "evidence_count": 1, "passed": False, "validator": "codex-cli"}, 5),
        event(R3, "outcome.final", "2026-10-16T08:33:00Z", {"status": "rejected", "validator": "codex-cli"}, 6),
    ]
    write_ndjson(d / "history.ndjson", events)
    write_json(d / "work-manifest.json", {
        "schema": "work-manifest.v1", "work_id": R3, "status": "rejected", "envelope_id": ENV_ID,
        "caller": {"submitted_by": "claude-frontier", "validated_by": "codex-cli"},
        "conditions": conditions(None), "route": route("am4-vllm", "code_fix", tokens=(1800, 400, 9000)),
        "attempts": [{"attempt": 1, "job_id": "job_r3", "ok": True}],
        "verdict": {"decision": "rejected", "caller_id": "codex-cli", "receipt_id": None,
                    "summary": "the cleared count is still off by one.", "evidence": ["streams/test_panel.py: 1 failed"],
                    "criteria": [{"id": "c1", "state": "failed", "note": "cleared=3 expected 4"}]},
    })

    # ---- R4: accepted, legacy "Review minutes: 3" prose, no fetch anywhere
    sub = "2026-10-16T09:50:00Z"
    d = root / R4
    write_json(d / "refs" / "envelope.json", envelope("night brief: streams row contract report (rerun)", "claude-frontier", sub,
                                                       ["states every REQUIRED field"]))
    events = [
        event(R4, "task.received", sub, {"criteria_count": 1, "envelope_id": ENV_ID, "intent_chars": 48,
                                        "intent_sha256": "5" * 64, "submitted_by": "claude-frontier"}, 1),
        event(R4, "step.dispatched", "2026-10-16T09:50:05Z", {"attempt": 1, "job_id": "job_r4", "model": "qwen3.8-27b",
                                                              "provider": "omen-dense-27b"}, 2),
        event(R4, "attempt.recorded", "2026-10-16T09:55:00Z", {"job_id": "job_r4", "ok": True}, 3),
        event(R4, "artifact.produced", "2026-10-16T09:55:00Z", {"artifact_id": "art_r4", "sha256": ART_SHA, "size": 3333}, 4),
        event(R4, "verification.recorded", "2026-10-16T10:40:00Z", {"criteria_count": 1, "decision": "accepted",
                                                                    "evidence_count": 1, "passed": True, "validator": "claude-frontier"}, 5),
        event(R4, "outcome.final", "2026-10-16T10:40:00Z", {"status": "accepted", "validator": "claude-frontier"}, 6),
    ]
    write_ndjson(d / "history.ndjson", events)
    write_json(d / "work-manifest.json", {
        "schema": "work-manifest.v1", "work_id": R4, "status": "accepted", "envelope_id": ENV_ID,
        "caller": {"submitted_by": "claude-frontier", "validated_by": "claude-frontier"},
        "conditions": conditions(None), "route": route("omen-dense-27b", "quote_retrieval", tokens=(2600, 650, 13000)),
        "attempts": [{"attempt": 1, "job_id": "job_r4", "ok": True}],
        "verdict": {"decision": "accepted", "caller_id": "claude-frontier", "receipt_id": None,
                    "summary": "rerun of the streams brief. Review minutes: 3 (source check). 200 words under cap.",
                    "evidence": ["streams/lib/receipt.py@aa85a801 REQUIRED tuple"],
                    "criteria": [{"criterion": "states every REQUIRED field", "status": "passed",
                                  "evidence": "ten fields quoted"}]},
    })


def make_ledger(root: Path) -> None:
    """HEARTH-ledger shaped: bodies in events.ndjson, an index.sqlite with offset/length per row."""
    def body(event_id: str, ts: str, caller: str, tool: str, args: dict) -> dict:
        return {"schema": "hearth-event.v1", "event_id": event_id, "ts": ts,
                "caller": {"id": caller, "runner_class": "frontier", "node": "omen"}, "tool": tool,
                "args_digest": "sha256:" + "9" * 64, "args_preview": json.dumps(args), "result_digest": None,
                "ok": True, "error": None, "duration_ms": 1.5, "cost": {"tokens_in": None, "tokens_out": None},
                "task_id": None, "task_class": "inference", "model": None, "backend": None, "routed_by": None,
                "occupancy": None, "error_code": None, "outcome": None, "profile": "unrestricted"}

    rows = [
        body("led-0001", "2026-10-01T07:19:00.000000Z", "claude-frontier", "watch_local_work", {"work_id": R2}),
        body("led-0002", "2026-10-01T07:20:00.000000Z", "claude-frontier", "get_local_work_artifact", {"work_id": R2}),
        body("led-0003", "2026-10-16T08:13:00.000000Z", "codex-cli", "get_local_work_artifact", {"work_id": R3}),
        body("led-0004", "2026-10-16T08:14:00.000000Z", "codex-cli", "get_local_work", {"work_id": R3}),
        body("led-0005", "2026-10-16T09:00:00.000000Z", "claude-frontier", "get_local_work_artifact", {"work_id": R3}),
        body("led-0006", "2026-10-16T09:30:00.000000Z", "claude-frontier", "get_local_work_artifact", {"work_id": OTHER}),
        body("led-0007", "2026-10-16T09:31:00.000000Z", "claude-frontier", "get_local_work_artifact", {"artifact_id": "art_x"}),
    ]
    root.mkdir(parents=True, exist_ok=True)
    ndjson = root / "events.ndjson"
    index = root / "index.sqlite"
    for p in (ndjson, index):
        if p.exists():
            p.unlink()
    offsets = []
    with ndjson.open("wb") as fh:
        for row in rows:
            data = (json.dumps(row) + "\n").encode("utf-8")
            offsets.append((fh.tell(), len(data)))
            fh.write(data)
    conn = sqlite3.connect(str(index))
    conn.execute("CREATE TABLE events ( event_id TEXT PRIMARY KEY, ts TEXT NOT NULL, caller_id TEXT NOT NULL, "
                 "runner_class TEXT NOT NULL, tool TEXT NOT NULL, ok INTEGER NOT NULL, duration_ms REAL NOT NULL, "
                 "offset INTEGER NOT NULL, length INTEGER NOT NULL, task_class TEXT, model TEXT, backend TEXT, "
                 "routed_by TEXT, occupancy TEXT, error_code TEXT)")
    conn.execute("CREATE INDEX idx_events_ts ON events(ts)")
    for row, (offset, length) in zip(rows, offsets):
        conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (row["event_id"], row["ts"], row["caller"]["id"], "frontier", row["tool"], 1, 1.5, offset, length,
                      "inference", None, None, None, None, None))
    conn.commit()
    conn.close()


def make_transcripts(root: Path) -> None:
    """Records carry only the keys the scanner is allowed to read, plus a shape-faithful text field that
    holds no content ("")."""
    def rec(sid: str, ts: str, kind: str, tool: tuple | None = None) -> dict:
        row = {"type": kind, "sessionId": sid, "timestamp": ts, "uuid": f"{sid[:8]}-{ts}", "cwd": "/tmp/fixture",
               "isSidechain": False, "version": "0.0.0"}
        if kind == "assistant":
            content = [{"type": "text", "text": ""}]
            if tool:
                content.append({"type": "tool_use", "id": "toolu_" + ts.replace(":", ""), "name": tool[0],
                                "input": {"work_id": tool[1]}})
            row["message"] = {"role": "assistant", "content": content}
        elif kind == "user":
            row["message"] = {"role": "user", "content": [{"type": "tool_result", "content": ""}]}
        return row

    def t(hms: str) -> str:
        return f"2026-10-16T{hms}.000Z"

    a = []
    a.append(rec(SID_A, t("10:04:00"), "user"))
    a.append(rec(SID_A, t("10:04:30"), "assistant", ("mcp__hearth__watch_local_work", R1)))
    a.append(rec(SID_A, t("10:05:00"), "assistant", ("mcp__hearth__get_local_work_artifact", R4)))
    a.append(rec(SID_A, t("10:05:30"), "user"))
    a.append(rec(SID_A, t("10:06:00"), "assistant", ("mcp__hearth__get_local_work_artifact", R1)))
    for m, s in ((6, 30), (7, 0), (7, 30), (8, 0), (8, 30), (9, 0), (9, 30), (10, 0)):
        a.append(rec(SID_A, t(f"10:{m:02d}:{s:02d}"), "user" if s else "assistant"))
    a.append(rec(SID_A, t("10:20:00"), "user"))
    for m in (21, 22, 23, 24):  # Monitor-tick burst: a record every 60 s
        a.append(rec(SID_A, t(f"10:{m:02d}:00"), "system"))
    a.append(rec(SID_A, t("10:24:40"), "assistant"))
    a.append(rec(SID_A, t("10:30:00"), "assistant", ("mcp__hearth__record_local_work_verdict", R1)))
    a.append(rec(SID_A, t("10:30:30"), "user"))
    a.append(rec(SID_A, t("10:35:00"), "assistant"))
    a.append(rec(SID_A, t("10:36:00"), "assistant"))  # a single 60 s gap is not a burst
    a.append(rec(SID_A, t("10:40:00"), "assistant", ("mcp__hearth__record_local_work_verdict", R4)))
    a.append(rec(SID_A, t("10:41:00"), "user"))
    # records without timestamps, as real transcripts have them
    a.insert(0, {"type": "custom-title", "sessionId": SID_A, "customTitle": ""})

    b = [
        {"type": "permission-mode", "sessionId": SID_B, "permissionMode": "default"},
        rec(SID_B, t("10:59:00"), "user"),
        rec(SID_B, t("11:00:00"), "assistant", ("mcp__hearth__get_local_work_artifact", R3)),
        rec(SID_B, t("11:00:30"), "user"),
        rec(SID_B, t("11:01:00"), "assistant", ("mcp__hearth__record_local_work_verdict", R3)),
        rec(SID_B, t("11:05:00"), "user"),
    ]
    # a decoy session that names the verdict tool in an attachment list but never calls it
    c = [
        {"type": "attachment", "sessionId": "cccccccc-3333-4333-8333-333333333333", "timestamp": t("12:00:00"),
         "attachment": {"tools": [{"name": "mcp__hearth__record_local_work_verdict",
                                   "schema": {"properties": {"work_id": {"type": "string"}}}}]}},
        rec("cccccccc-3333-4333-8333-333333333333", t("12:00:30"), "assistant", ("mcp__hearth__get_local_work", OTHER)),
    ]
    proj = root / "-fixture-project"
    proj.mkdir(parents=True, exist_ok=True)
    write_ndjson(proj / f"{SID_A}.jsonl", a)
    write_ndjson(proj / f"{SID_B}.jsonl", b)
    write_ndjson(proj / "cccccccc-3333-4333-8333-333333333333.jsonl", c)


def make_corpus(root: Path) -> None:
    rows = [
        {"schema": "precursor-run.v2", "run_id": R2, "group_id": "g_streams_brief", "intent_sha256": "3" * 64,
         "intent_text": "night brief: streams row contract report"},
        {"schema": "precursor-run.v2", "run_id": R4, "group_id": "g_streams_brief", "intent_sha256": "5" * 64,
         "intent_text": "night brief: streams row contract report (rerun)"},
        {"schema": "precursor-run.v1", "run_id": R1, "group_id": "g_ignored_v1"},
    ]
    write_ndjson(root / "runs.jsonl", rows)


def main() -> None:
    for sub in ("runs", "ledger", "transcripts", "corpus"):
        shutil.rmtree(HERE / sub, ignore_errors=True)
    make_runs(HERE / "runs")
    make_ledger(HERE / "ledger")
    make_transcripts(HERE / "transcripts")
    make_corpus(HERE / "corpus")
    print(json.dumps({"runs": [R1, R2, R3, R4], "sessions": [SID_A, SID_B]}, indent=1))


if __name__ == "__main__":
    main()
