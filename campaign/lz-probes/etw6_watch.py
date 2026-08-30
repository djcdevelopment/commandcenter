"""ETW6 watcher - UNELEVATED. Detection, snapshot orchestration, capture identity.

The privileged component (etw6_session.ps1) only owns the ETW session lifecycle. This
process performs detection and snapshotting, and never reconfigures or restarts tracing.
Verified 2026-08-30: an unelevated process CAN copy the live circular ETL (205.63 MB
copied while the session was writing).

Trigger is EXTERNAL to ETW: the existing 5-minute keep-alive deep probe detects the state
from its own ledger. We add no requests.

Snapshot policy. The ring holds ~42 min at the measured 7.3 MB/s filtered rate, and
episodes run 5-10 min, so ONE copy taken after recovery would contain the whole arc:
    healthy prehistory -> onset -> degraded dwell -> recovery
Two are taken anyway:
  * AT TRIGGER   - guarantees prehistory+onset survive even if the episode outruns the ring
  * AFTER RECOVERY (+ settle margin) - the full arc in a single file
A copy is bounded by the ring size; E: has ~2.2 TB free.

⚠ A snapshot loses the buffers still in memory at copy time (up to 64 KB x 256 = 16 MB,
~2 s at the measured rate). That is acceptable because the value is in the PREHISTORY, but
it means the last ~2 s before a snapshot are not in the file. Recorded, not hidden.

Usage: etw6_watch.py [--once]
"""
import io, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone

REC = r"E:\work\battlemage\ff-probes\etw-recorder"
ETL = os.path.join(REC, "lz_dxgk_ring.etl")
MANIFEST = os.path.join(REC, "session-manifest.json")
KA = r"C:\work\commandcenter\hearth\var\arc-keepalive.jsonl"
CAPTURES = os.path.join(REC, "captures")

DEGRADED_TOK_S = 90.0     # deep-probe decode below this = degraded (ADR-0044 observed regimes)
SETTLE_S = 120            # keep recording this long after recovery before the arc snapshot
POLL_S = 20


def now():
    return datetime.now(timezone.utc)


def read_deep_probes(since_epoch=0.0):
    """Deep probes (predicted_n>1) from the keep-alive ledger. We add no requests."""
    out = []
    if not os.path.exists(KA):
        return out
    with io.open(KA, encoding="utf-8", errors="replace") as fh:
        for line in fh.readlines()[-400:]:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("probe") != "ARC-KEEPALIVE" or r.get("decode_tok_s") is None:
                continue
            try:
                ts = datetime.fromisoformat(r["ts"]).timestamp()
            except Exception:
                continue
            if ts > since_epoch:
                out.append({"epoch": ts, "ts": r["ts"], "decode": r["decode_tok_s"],
                            "prompt_ms": r.get("prompt_ms"),
                            "degraded": bool(r.get("decode_degraded"))})
    out.sort(key=lambda x: x["epoch"])
    return out


def session_manifest():
    try:
        return json.load(io.open(MANIFEST, encoding="utf-8-sig"))
    except Exception:
        return {}


def snapshot(tag, trigger, extra):
    """Copy the live ring + write a capture manifest carrying full identity."""
    os.makedirs(CAPTURES, exist_ok=True)
    stamp = now().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(CAPTURES, "cap-%s-%s" % (stamp, tag))
    dst = base + ".etl"
    t0 = time.time()
    shutil.copy2(ETL, dst)
    copy_s = time.time() - t0
    sm = session_manifest()
    try:
        commit = subprocess.check_output(
            ["git", "-C", r"C:\work\commandcenter", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        commit = None
    man = {
        "tag": tag,
        "captured_utc": now().isoformat(),
        "etl": dst,
        "etl_bytes": os.path.getsize(dst),
        "copy_seconds": round(copy_s, 2),
        "buffers_lost_note": "up to 16 MB (~2 s) still in memory at copy time is NOT in this file",
        # --- capture identity, so a binary ETL stays interpretable many ADRs from now ---
        "server_pid": sm.get("server_pid"),
        "server_start_utc": sm.get("server_start_utc"),
        "baseline_epoch": sm.get("baseline_epoch"),
        "etw_config_sha256_16": sm.get("etw_config_sha256_16"),
        "etw_keywords": sm.get("keywords"),
        "etw_level": sm.get("level"),
        "ring_mb": sm.get("ring_mb"),
        "ring_horizon_note": sm.get("ring_horizon_note"),
        "session_started_utc": sm.get("started_utc"),
        "analyzer_commit": commit,
        "healthy_floor": sm.get("healthy_floor"),
        # --- QUEUE CLASSIFICATION: pinned, never rediscovered per trace ---
        "deep_compute_queues": {
            "values": ["0xFFFFBB041C43A150", "0xFFFFBB03C9FACB40"],
            "why": "ETW1/ETW3 healthy arms: 320 and 288 submits, in-flight depth 8 and 7. "
                   "The third queue 0xFFFFBB03C9FAAD00 had exactly 32 submits (== n_predict), "
                   "depth<=1, 0.054 ms execution and a 9.75 ms period: a per-token CLOCK, "
                   "excluded from the compute statistic.",
            "warning": "handles are per-process-epoch. If server_pid differs from the healthy "
                       "baseline's, these values are STALE and must be re-derived before any "
                       "comparison. Do NOT let the analyzer silently rediscover a different "
                       "queue set per trace.",
        },
        "trigger": trigger,
    }
    man.update(extra or {})
    io.open(base + ".json", "w", encoding="utf-8").write(json.dumps(man, indent=2))
    print("  snapshot %s -> %s (%.1f MB, %.1fs)" % (tag, dst, man["etl_bytes"] / 1e6, copy_s))
    return man


def _arg(name, default=None):
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


def main():
    global KA, CAPTURES, SETTLE_S
    once = "--once" in sys.argv
    # Dry-run overrides. The synthetic-trigger rehearsal MUST NOT write into the real
    # keep-alive ledger: that file is the campaign's primary rate instrument and a fake
    # row in it would contaminate every later analysis. So the rehearsal points at its
    # own ledger and its own capture directory instead.
    KA = _arg("--ledger", KA)
    CAPTURES = _arg("--captures", CAPTURES)
    SETTLE_S = int(_arg("--settle", SETTLE_S))
    if not os.path.exists(ETL):
        print("no live ring at %s -- start it with etw6_session.ps1 -Start (elevated)" % ETL)
        return 2
    print("watching %s" % KA)
    print("ring: %s" % ETL)
    seen = time.time() - 60
    episode = None
    while True:
        probes = read_deep_probes(seen)
        for p in probes:
            seen = max(seen, p["epoch"])
            deg = p["degraded"] or p["decode"] < DEGRADED_TOK_S
            state = "DEGRADED" if deg else "healthy"
            print("  %s decode=%7.2f prompt_ms=%-6s %s" % (p["ts"][11:19], p["decode"], p["prompt_ms"], state))
            if deg and episode is None:
                episode = {"trigger_ts": p["ts"], "trigger_decode": p["decode"],
                           "trigger_prompt_ms": p["prompt_ms"], "recovered_ts": None}
                print("  >>> TRIGGER: snapshotting prehistory+onset")
                snapshot("onset", episode, {"phase": "at-trigger"})
            elif not deg and episode is not None and episode["recovered_ts"] is None:
                episode["recovered_ts"] = p["ts"]
                episode["recovery_decode"] = p["decode"]
                print("  >>> RECOVERED. settling %ds then capturing the full arc" % SETTLE_S)
                time.sleep(SETTLE_S)
                snapshot("arc", episode, {"phase": "post-recovery",
                                          "arc": "prehistory -> onset -> dwell -> recovery"})
                episode = None
                if once:
                    return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
