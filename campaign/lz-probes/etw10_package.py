"""ETW10: package a CONTINUOUS ring snapshot into the report.json the ETW2/ETW4 readers eat.

WHAT IT CONSUMES
  1. the GPU trace, either
       --dump <tracerpt XML>   PRIMARY path: an already-converted dump; or
       --etl  <cap-*.etl>      a ring snapshot from etw6_watch.py, converted here.
  2. --manifest <cap-*.json>   the capture manifest etw6_watch.py writes beside the .etl.
       server_pid is REQUIRED. etw4_depth does `str(rep.get("server_pid"))`, so a missing
       pid silently becomes the string "None", matches no event, and reports server_share
       0% on every queue instead of failing. That is the campaign's signature failure --
       convincing empty output -- so it is a hard error here.
  3. --requests <rows.jsonl>   a load-harness request log: the rows
       campaign/qwen38/qwen38_campaign.py `load` writes (make_row). Required per row:
       run_id, client_id, started_at, completed_at, prompt_ms, predicted_ms.

WHAT IT PRODUCES
  A report.json in exactly the shape etw1_feasibility.ps1 emits and etw2_join.main /
  etw4_depth.main read: {server_pid, arms:[{label,start_utc,end_utc,prompt_ms,
  predicted_ms,...}], traces:[{path,bytes,dump,dump_bytes,...}]}.

  The readers pair a trace with an arm by DERIVING the arm label from the dump FILENAME:
      lab = "etw-" + dump.split("-r")[-1].split(".")[0]
  (etw2_join.py:259, etw4_depth.py:137 -- the same expression, twice). So there is exactly
  ONE arm per trace entry, and a trace's filename is not cosmetic: it is the join key.
  ETW1 got one arm per short trace for free. A ring snapshot inverts that -- ONE long
  trace, MANY request windows -- so this packager materialises one `<stem>-rN.dump.xml`
  NAME per arm (hard link where the volume allows, copy otherwise) over the one underlying
  XML, and records dump_link / dump_source on every trace so nobody mistakes N names for N
  independent captures. trace_label() below reimplements the readers' expression verbatim
  and every emitted name is asserted against it before the report is written.

TRACERPT ELEVATION -- MEASURED, NOT ASSUMED
  tracerpt on an EXISTING .etl does NOT need elevation on this box. Measured 2026-09-09 as
  OMEN\\derek with IsInRole(Administrator) = False:
      tracerpt E:\\work\\battlemage\\ff-probes\\etw-20260830\\etw1-20260830-043447-r1.etl
               -o <scratch>\\elevtest-r1.dump.xml -of XML -y
      -> exit 0 in 2.2 s, 70,148,788 bytes, byte-identical in size to the dump the
         ELEVATED etw1_feasibility.ps1 run produced from the same .etl.
  Only the ETW SESSION lifecycle needs elevation (etw6_session.ps1); post-processing does
  not. So --etl works unelevated and the XML-dump path is offered for convenience and for
  budgeted conversion, not to dodge a privilege wall.

  What DOES bite at ring scale is that tracerpt reads the .etl TWICE and writes nothing
  during the first pass. Measured on cap-20260901-152356-arc.etl (19,327,352,832 B),
  unelevated: 12.4 GB read at ~150 s with 128 bytes written and the working set flat at
  ~93 MB; the whole 19.33 GB read before any XML appeared; at the 2 GB stop the process had
  read 39.13 GB -- two full passes -- and written 2.51 GB. So a ring conversion costs a
  ~12 min blind pre-pass, and the "0 bytes written" it shows meanwhile is progress, not a
  hang. --max-dump-mb bounds the output: the converter is stopped at the budget and the
  partial XML is REPAIRED to well-formed by cutting after the last complete </Event> and
  closing <Events>. tracerpt emits in timestamp order, so a budgeted dump holds a
  CONTIGUOUS EARLIEST slice -- 2.51 GB of that capture held 164 s of DxgKrnl events
  (2026-09-01T14:35:27Z .. 14:38:11Z) out of a ~48 min ring. It is stamped truncated:true,
  and arms must be aligned to dump_span, NOT to the .etl's full horizon. For that capture
  the INC-A onset at 15:16:41Z is NOT in a 2.5 GB prefix.

AGGREGATION, AND ITS ONE TRAP
  An arm's window is [min(started_at), max(completed_at)] over its group; prompt_ms and
  predicted_ms are SUMS over the group's rows, which is the generalisation of ETW1's
  single-request arm ("server-side work for comparison" against the wall window). Under
  concurrency > 1 that sum EXCEEDS the arm's wall time by construction, so every arm also
  carries concurrency_observed, n_requests and the *_mean values. Read them together.

Usage: etw10_package.py --manifest cap-X.json --requests rows.jsonl
                        (--dump D.xml | --etl cap-X.etl) --out report.json
       etw10_package.py --describe (--dump D.xml | --requests rows.jsonl)
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from etw2_join import epoch  # noqa: E402  the readers' own ISO parser, not a lookalike

XML_ROOT = "Events"
EVENT_CLOSE = b"</Event>"
SYSTEMTIME = re.compile(rb'SystemTime="([^"]+)"')
REQUIRED_ROW_FIELDS = ("run_id", "client_id", "started_at", "completed_at",
                       "prompt_ms", "predicted_ms")


class PackagingError(Exception):
    """Anything that would otherwise produce a plausible-but-empty report."""


class MalformedRequestRow(PackagingError):
    def __init__(self, path, lineno, reason, raw=None):
        self.path, self.lineno, self.reason = path, lineno, reason
        snippet = ("  |  %s" % raw[:160]) if raw else ""
        super(MalformedRequestRow, self).__init__(
            "%s line %d: %s%s" % (path, lineno, reason, snippet))


# --------------------------------------------------------------------------- labels

def trace_label(dump_path):
    """The readers' join key, reimplemented VERBATIM.

    etw2_join.py:259 and etw4_depth.py:137 both compute
        lab = "etw-" + dump.split("-r")[-1].split(".")[0]
    on the dump path as written in the report. It splits on the LAST "-r" anywhere in the
    whole path, so a directory such as ...\\etw-recorder\\... is a live hazard: it makes the
    label depend on the directory unless the FILENAME contributes a later "-r". Every name
    this packager emits ends in -rN.dump.xml for exactly that reason, and emit_trace_names
    asserts the result.
    """
    return "etw-" + str(dump_path).split("-r")[-1].split(".")[0]


def arm_label(index):
    return "etw-%d" % index


def package_dump_name(stem, index):
    return "%s-r%d.dump.xml" % (stem, index)


# ------------------------------------------------------------------------ timestamps

def iso_utc(dt):
    """Always six fractional digits and a Z.

    isoformat() drops the fractional part entirely when microsecond == 0, which walks
    straight out of etw2_join.ISO's `\\.(\\d+)` branch. Format explicitly instead.
    """
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse_ts(value, what):
    """ISO -> (epoch seconds, normalised UTC string). Raises rather than returning None."""
    if not isinstance(value, str) or not value.strip():
        raise PackagingError("%s is not an ISO timestamp string: %r" % (what, value))
    ep = epoch(value)
    if ep is None:
        raise PackagingError("%s is not parseable by etw2_join.epoch: %r" % (what, value))
    return ep, iso_utc(datetime.fromtimestamp(ep, timezone.utc))


def assert_round_trip(label, iso, ep):
    """The 28800 s bug, fixtured as a runtime guard.

    ETW1 revision 1 wrote arm epochs that were 8 h wrong because [datetime]'1970-01-01Z'
    parses as LOCAL in PS 5.1, and the result was an EMPTY join rather than an error. Every
    string this packager writes is re-read through the readers' own epoch() before the
    report leaves the process.
    """
    back = epoch(iso)
    if back is None:
        raise PackagingError("arm %s: emitted %r does not survive etw2_join.epoch()" % (label, iso))
    if abs(back - ep) > 0.002:
        raise PackagingError("arm %s: %r re-reads as %.3f, not %.3f (delta %.1f s)"
                             % (label, iso, back, ep, back - ep))


# -------------------------------------------------------------------------- manifest

def load_manifest(path):
    """cap-*.json from etw6_watch.snapshot(). server_pid is mandatory."""
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            man = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PackagingError("cannot read capture manifest %s: %s" % (path, exc))
    if not isinstance(man, dict):
        raise PackagingError("capture manifest %s is not a JSON object" % path)
    pid = man.get("server_pid")
    if pid is None:
        raise PackagingError(
            "capture manifest %s has no server_pid. etw4_depth does str(rep['server_pid']), "
            "so packaging this would report server_share 0%% on every queue instead of "
            "failing. Refusing." % path)
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        raise PackagingError("capture manifest %s: server_pid %r is not an integer" % (path, pid))
    return {
        "server_pid": pid,
        "tag": man.get("tag"),
        "etl": man.get("etl"),
        "etl_bytes": man.get("etl_bytes"),
        "captured_utc": man.get("captured_utc"),
        "phase": man.get("phase"),
        "trigger": man.get("trigger"),
        "server_start_utc": man.get("server_start_utc"),
        "session_started_utc": man.get("session_started_utc"),
        "etw_keywords": man.get("etw_keywords"),
        "etw_level": man.get("etw_level"),
        "etw_config_sha256_16": man.get("etw_config_sha256_16"),
        "ring_mb": man.get("ring_mb"),
        "analyzer_commit": man.get("analyzer_commit"),
        "healthy_floor": man.get("healthy_floor"),
        "deep_compute_queues": man.get("deep_compute_queues"),
        "buffers_lost_note": man.get("buffers_lost_note"),
        "manifest_path": os.path.abspath(path),
    }


# ------------------------------------------------------------------------- requests

def _number(row, field, lineno, path):
    v = row.get(field)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise MalformedRequestRow(path, lineno, "%s is present but not a number: %r" % (field, v))
    return float(v)


def load_requests(path, require_success=True):
    """JSONL -> (rows, excluded). Malformed rows RAISE; they are never skipped.

    Rejected loudly (raises MalformedRequestRow, packaging aborts):
      * a non-blank line that is not JSON, or is not a JSON object
      * any of REQUIRED_ROW_FIELDS missing from the object
      * started_at / completed_at unparseable, or completed_at before started_at
      * prompt_ms / predicted_ms present but not numeric
      * a row that carries timings of null while claiming success -- llama-server always
        returns timings on a successful completion, so that pair is incoherent

    Excluded and COUNTED (never silent -- the tally reaches the report):
      * rows whose success or valid field is explicitly false. Their wall time is a failed
        request's, not GPU work, and letting one stretch an arm window would inflate
        depth-0 exactly the way ETW4's raw-window edge inflation does.
    """
    rows, excluded = [], []
    if not os.path.exists(path):
        raise PackagingError("request log not found: %s" % path)
    with io.open(path, encoding="utf-8-sig") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise MalformedRequestRow(path, lineno, "not JSON (%s)" % exc, line)
            if not isinstance(row, dict):
                raise MalformedRequestRow(path, lineno, "not a JSON object", line)
            missing = [f for f in REQUIRED_ROW_FIELDS if f not in row]
            if missing:
                raise MalformedRequestRow(path, lineno, "missing required field(s): %s"
                                          % ", ".join(missing), line)
            if row.get("run_id") in (None, ""):
                raise MalformedRequestRow(path, lineno, "run_id is empty", line)
            try:
                s_ep, s_iso = parse_ts(row["started_at"], "started_at")
                c_ep, c_iso = parse_ts(row["completed_at"], "completed_at")
            except PackagingError as exc:
                raise MalformedRequestRow(path, lineno, str(exc), line)
            if c_ep < s_ep:
                raise MalformedRequestRow(
                    path, lineno, "completed_at %s precedes started_at %s"
                    % (row["completed_at"], row["started_at"]), line)
            prompt_ms = _number(row, "prompt_ms", lineno, path)
            predicted_ms = _number(row, "predicted_ms", lineno, path)

            ok = True
            reason = None
            if require_success:
                if row.get("success") is False:
                    ok, reason = False, "success=false"
                elif row.get("valid") is False:
                    ok, reason = False, "valid=false (%s)" % row.get("failure_class")
            if not ok:
                excluded.append({"line": lineno, "run_id": row.get("run_id"),
                                 "client_id": row.get("client_id"), "reason": reason})
                continue
            if prompt_ms is None or predicted_ms is None:
                raise MalformedRequestRow(
                    path, lineno,
                    "prompt_ms/predicted_ms is null on a row not marked failed "
                    "(success=%r valid=%r). llama-server always returns timings on a "
                    "successful completion, so this row is incoherent -- fix the log or "
                    "mark the row failed; it will not be silently dropped"
                    % (row.get("success"), row.get("valid")), line)
            rows.append({
                "line": lineno,
                "run_id": row["run_id"],
                "client_id": row["client_id"],
                "start_epoch": s_ep, "start_utc": s_iso,
                "end_epoch": c_ep, "end_utc": c_iso,
                "prompt_ms": prompt_ms, "predicted_ms": predicted_ms,
                "prompt_n": row.get("prompt_tokens"),
                "predicted_n": row.get("predicted_tokens") or row.get("generated_tokens"),
            })
    if not rows:
        raise PackagingError(
            "%s yielded no usable rows (%d excluded as failed). Refusing to write a report "
            "with zero arms: etw4_depth would KeyError on the first trace, or worse, print "
            "an empty comparison." % (path, len(excluded)))
    return rows, excluded


def group_requests(rows, group_by):
    """run -> one arm per run_id; client -> one arm per (run_id, client_id)."""
    if group_by not in ("run", "client"):
        raise PackagingError("--group-by must be run or client, not %r" % group_by)
    groups = {}
    for r in rows:
        key = r["run_id"] if group_by == "run" else "%s/c%s" % (r["run_id"], r["client_id"])
        groups.setdefault(key, []).append(r)
    ordered = sorted(groups.items(), key=lambda kv: min(r["start_epoch"] for r in kv[1]))
    return ordered


def max_overlap(rows):
    """Peak simultaneous in-flight requests -- the number that makes the ms SUM readable."""
    marks = []
    for r in rows:
        marks.append((r["start_epoch"], 1))
        marks.append((r["end_epoch"], -1))
    # At a shared instant a completion is processed BEFORE the next submission: a request
    # ending exactly when another starts is sequential, not concurrent.
    marks.sort(key=lambda m: (m[0], m[1]))
    cur = peak = 0
    for _, d in marks:
        cur += d
        peak = max(peak, cur)
    return peak


def build_arms(ordered_groups, group_by):
    arms = []
    for index, (key, group) in enumerate(ordered_groups, 1):
        lo = min(r["start_epoch"] for r in group)
        hi = max(r["end_epoch"] for r in group)
        start_iso = iso_utc(datetime.fromtimestamp(lo, timezone.utc))
        end_iso = iso_utc(datetime.fromtimestamp(hi, timezone.utc))
        label = arm_label(index)
        assert_round_trip(label, start_iso, lo)
        assert_round_trip(label, end_iso, hi)
        prompt_sum = sum(r["prompt_ms"] for r in group)
        predicted_sum = sum(r["predicted_ms"] for r in group)
        n = len(group)
        arm = {
            "label": label,
            "kind": "ring",
            "group_by": group_by,
            "group_key": key,
            "start_utc": start_iso,
            "end_utc": end_iso,
            "start_epoch": round(lo, 3),
            "end_epoch": round(hi, 3),
            "wall_s": round(hi - lo, 6),
            "n_requests": n,
            "concurrency_observed": max_overlap(group),
            # ETW1 carried one request's server-side work per arm. For a group the
            # generalisation is the sum; under concurrency it exceeds wall_s by design.
            "prompt_ms": round(prompt_sum, 4),
            "predicted_ms": round(predicted_sum, 4),
            "prompt_ms_sum": round(prompt_sum, 4),
            "predicted_ms_sum": round(predicted_sum, 4),
            "prompt_ms_mean": round(prompt_sum / n, 4),
            "predicted_ms_mean": round(predicted_sum / n, 4),
            "request_lines": [r["line"] for r in group],
        }
        pn = [r["predicted_n"] for r in group if isinstance(r["predicted_n"], (int, float))]
        if pn:
            arm["predicted_n"] = sum(pn)
            arm["wall_per_token_ms"] = round(predicted_sum / sum(pn), 4) if sum(pn) else None
        arms.append(arm)
    return arms


# ------------------------------------------------------------------------ conversion

def repair_truncated_xml(path):
    """Cut a killed tracerpt dump back to its last complete </Event> and close <Events>.

    </Event> is a prefix of </Events>, so a naive backwards search would happily land
    inside a root close tag; the next byte is checked.
    """
    size = os.path.getsize(path)
    chunk = 1 << 20
    with io.open(path, "r+b") as fh:
        pos = size
        cut = -1
        tail = b""
        while pos > 0 and cut < 0:
            start = max(0, pos - chunk)
            fh.seek(start)
            buf = fh.read(pos - start) + tail
            idx = buf.rfind(EVENT_CLOSE)
            while idx >= 0:
                after = buf[idx + len(EVENT_CLOSE):idx + len(EVENT_CLOSE) + 1]
                if after != b"s":
                    cut = start + idx + len(EVENT_CLOSE)
                    break
                idx = buf.rfind(EVENT_CLOSE, 0, idx)
            tail = buf[:len(EVENT_CLOSE)]
            pos = start
        if cut < 0:
            raise PackagingError(
                "%s holds no complete </Event>; the conversion budget was too small to "
                "produce a usable dump" % path)
        fh.seek(cut)
        fh.write(b"\n</%s>\n" % XML_ROOT.encode("ascii"))
        fh.truncate()
    return {"repaired": True, "bytes_before": size, "bytes_after": os.path.getsize(path),
            "bytes_discarded": size - cut}


def dump_is_closed(path, tail_bytes=4096):
    """Does this dump end with its root close tag? Cheap; reads the tail only."""
    size = os.path.getsize(path)
    with io.open(path, "rb") as fh:
        fh.seek(max(0, size - tail_bytes))
        return b"</%s>" % XML_ROOT.encode("ascii") in fh.read()


def require_closed_dump(path, repair=False):
    """A truncated dump must fail HERE, not two minutes into the reader's iterparse.

    etw2_join.events() streams with iterparse, so a dump cut mid-event yields real events
    and THEN raises ParseError -- after a long scan, and after a partial analysis has
    already been printed. Check the tail up front instead.
    """
    if dump_is_closed(path):
        return None
    if not repair:
        raise PackagingError(
            "%s does not end with </%s>: it is a truncated conversion. etw2_join.events() "
            "would stream most of it and only then raise ParseError, after printing a "
            "partial analysis. Re-run with --repair-dump to cut it back to the last "
            "complete </Event> and close the root." % (path, XML_ROOT))
    return repair_truncated_xml(path)


def convert_etl(etl, dump, max_dump_mb=None, tracerpt_exe="tracerpt", poll_s=5.0,
                timeout_s=None, verbose=True):
    """tracerpt <etl> -o <dump> -of XML -y. Unelevated -- measured, see the module docstring.

    max_dump_mb bounds a ring-scale conversion: the converter is stopped once the dump
    passes the budget and the partial XML is repaired to well-formed. tracerpt reads the
    WHOLE .etl before it writes anything, so the budget bounds output size and disk, not
    the pre-pass wall time.
    """
    if not os.path.exists(etl):
        raise PackagingError("etl not found: %s" % etl)
    outdir = os.path.dirname(os.path.abspath(dump))
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    if os.path.exists(dump):
        os.remove(dump)
    cmd = [tracerpt_exe, etl, "-o", dump, "-of", "XML", "-y"]
    log = dump + ".tracerpt.log"
    if verbose:
        print("  tracerpt: %s" % " ".join(cmd))
        print("  converter log: %s" % log)
    t0 = time.time()
    # tracerpt's stdout goes to a FILE, not a pipe: a ring-scale conversion runs for many
    # minutes and an unread pipe that fills would deadlock the converter, which would look
    # exactly like a hang.
    budget = None if max_dump_mb is None else int(max_dump_mb) * (1 << 20)
    truncated = False
    with io.open(log, "wb") as logfh:
        proc = subprocess.Popen(cmd, stdout=logfh, stderr=subprocess.STDOUT)
        while proc.poll() is None:
            time.sleep(poll_s)
            size = os.path.getsize(dump) if os.path.exists(dump) else 0
            if verbose:
                print("    %6.0fs  dump=%14d bytes" % (time.time() - t0, size))
            if budget is not None and size > budget:
                if verbose:
                    print("    budget %d MB reached -- stopping the converter" % max_dump_mb)
                proc.kill()
                truncated = True
                break
            if timeout_s is not None and time.time() - t0 > timeout_s:
                proc.kill()
                truncated = True
                if verbose:
                    print("    timeout %.0fs reached -- stopping the converter" % timeout_s)
                break
        proc.wait(timeout=120)
    rc = proc.returncode
    if not os.path.exists(dump):
        tail = ""
        try:
            with io.open(log, "rb") as fh:
                tail = fh.read()[-800:].decode("utf-8", "replace")
        except OSError:
            pass
        raise PackagingError("tracerpt produced no dump (rc=%s): %s" % (rc, tail))
    info = {"tracerpt_rc": rc, "tracerpt_log": log,
            "elapsed_s": round(time.time() - t0, 1), "truncated": truncated}
    if truncated:
        info.update(repair_truncated_xml(dump))
    info["dump_bytes"] = os.path.getsize(dump)
    return info


# ------------------------------------------------------------------------- dump span

def dump_span(path, verbose=False):
    """A dump's time span, over DxgKrnl PROVIDER events only. Raw scan, not an XML parse.

    Taking min/max over EVERY SystemTime in the file is wrong, and wrong in the dangerous
    direction. tracerpt emits ETW header events whose SystemTime is the SESSION start, and
    for a wrapped circular ring that is far older than anything the ring still holds.
    Measured on cap-20260901-152356-arc (2026-09-09):
        every SystemTime      2026-08-30T13:00:45Z .. 2026-09-01T14:38:11Z  (178,646 s)
        DxgKrnl events only   2026-09-01T14:35:27Z .. 2026-09-01T14:38:11Z  (164 s)
    a 1000x inflation, and the inflated bound would have licensed an arm anywhere in two
    days of wall clock against a trace holding under three minutes of events. So the scan
    is a two-token state machine: a SystemTime counts only when the enclosing event's
    Provider carries Name="Microsoft-Windows-DxgKrnl". The raw bound is still reported, as
    raw_*, because it names the session the ring belongs to.

    tracerpt emits in timestamp order (verified: the events either side of the header pair
    ascend monotonically), so a budget-truncated dump holds a CONTIGUOUS EARLIEST slice.
    """
    if not os.path.exists(path):
        raise PackagingError("dump not found: %s" % path)
    lo = hi = raw_lo = raw_hi = None
    n = n_raw = 0
    size = os.path.getsize(path)
    scan = re.compile(rb'(<Provider Name="Microsoft-Windows-DxgKrnl")|SystemTime="([^"]+)"')
    with io.open(path, "rb") as fh:
        carry = b""
        armed = False
        while True:
            buf = fh.read(1 << 22)
            if not buf:
                break
            data = carry + buf
            for m in scan.finditer(data):
                if m.group(1):
                    armed = True
                    continue
                ep = epoch(m.group(2).decode("ascii", "replace"))
                if ep is None:
                    armed = False
                    continue
                n_raw += 1
                raw_lo = ep if raw_lo is None else min(raw_lo, ep)
                raw_hi = ep if raw_hi is None else max(raw_hi, ep)
                if armed:
                    n += 1
                    lo = ep if lo is None else min(lo, ep)
                    hi = ep if hi is None else max(hi, ep)
                    armed = False
            # overlap so a Provider/SystemTime pair split across chunks still pairs up;
            # re-seen matches only re-min/max, which is idempotent
            carry = data[-1024:]
            if verbose:
                print("    scanned %d DxgKrnl timestamps (%d total)" % (n, n_raw))
    if lo is None:
        raise PackagingError(
            "%s carries no Microsoft-Windows-DxgKrnl event with a parseable SystemTime "
            "(%d non-DxgKrnl timestamps seen). It is not a DxgKrnl dump, or it holds only "
            "ETW header events." % (path, n_raw))
    return {"dump": os.path.abspath(path), "dump_bytes": size,
            "n_timestamps": n, "n_timestamps_all_providers": n_raw,
            "start_epoch": lo, "end_epoch": hi,
            "start_utc": iso_utc(datetime.fromtimestamp(lo, timezone.utc)),
            "end_utc": iso_utc(datetime.fromtimestamp(hi, timezone.utc)),
            "span_s": round(hi - lo, 3),
            "raw_start_utc": iso_utc(datetime.fromtimestamp(raw_lo, timezone.utc)),
            "raw_end_utc": iso_utc(datetime.fromtimestamp(raw_hi, timezone.utc)),
            "raw_span_s": round(raw_hi - raw_lo, 3),
            "span_note": ("start/end cover DxgKrnl events only; raw_* includes the ETW "
                          "header events, whose SystemTime is the SESSION start and is "
                          "far older than a wrapped ring's oldest retained event")}


def check_arms_against_span(arms, span, allow_outside=False):
    """An arm entirely outside the trace yields depth-0 = 100% and looks like starvation."""
    verdicts = []
    for a in arms:
        inside = not (a["end_epoch"] < span["start_epoch"] or a["start_epoch"] > span["end_epoch"])
        overlap = max(0.0, min(a["end_epoch"], span["end_epoch"])
                      - max(a["start_epoch"], span["start_epoch"]))
        verdicts.append({"label": a["label"], "intersects_dump": inside,
                         "overlap_s": round(overlap, 6),
                         "covered_fraction": round(overlap / (a["wall_s"] or 1e-12), 4)})
        a["dump_overlap_s"] = round(overlap, 6)
    bad = [v["label"] for v in verdicts if not v["intersects_dump"]]
    if bad and not allow_outside:
        raise PackagingError(
            "arm(s) %s lie entirely outside the dump span %s .. %s. Packaging them would "
            "hand etw4_depth a window with no events, which reports depth-0 = 100%% and "
            "reads as total starvation. Fix the alignment, or pass --allow-outside-span to "
            "record it deliberately." % (", ".join(bad), span["start_utc"], span["end_utc"]))
    return verdicts


# --------------------------------------------------------------------- trace emission

def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def emit_trace_names(arms, source_dump, package_dir, stem, source_etl=None,
                     source_etl_bytes=None, verbose=True):
    """One `<stem>-rN.dump.xml` per arm over ONE underlying XML. Link, else copy."""
    os.makedirs(package_dir, exist_ok=True)
    src = os.path.abspath(source_dump)
    dump_bytes = os.path.getsize(src)
    traces = []
    for index, arm in enumerate(arms, 1):
        target = os.path.abspath(os.path.join(package_dir, package_dump_name(stem, index)))
        got = trace_label(target)
        if got != arm["label"]:
            raise PackagingError(
                "emitted trace name %s derives label %r, but its arm is %r. The readers "
                "join on this filename; refusing to write a report they would KeyError on."
                % (target, got, arm["label"]))
        if os.path.abspath(target) == src:
            how = "same-file"
        elif os.path.exists(target) and _same_file(target, src):
            # already a link to THIS source. Identity, not a size coincidence: a stale
            # name of equal size pointing at a different capture is exactly the kind of
            # silent mismatch that makes a report unreadable a month later.
            how = "hardlink (existing)"
        else:
            if os.path.exists(target):
                os.remove(target)
            try:
                os.link(src, target)
                how = "hardlink"
            except OSError:
                shutil.copyfile(src, target)
                how = "copy"
        if verbose:
            print("  trace %-8s %s  (%s)" % (arm["label"], target, how))
        traces.append({
            "path": source_etl or src,
            "bytes": source_etl_bytes if source_etl_bytes is not None else dump_bytes,
            "dump": target,
            "dump_bytes": os.path.getsize(target),
            "dump_source": src,
            "dump_link": how,
            "arm": arm["label"],
        })
    return traces


# ------------------------------------------------------------------------------ main

def build_report(manifest_path, requests_path, dump_path, out_path,
                 group_by="run", package_dir=None, etl_path=None, etl_info=None,
                 require_success=True, span_check=True, allow_outside_span=False,
                 repair_dump=False, verbose=True):
    if not os.path.exists(dump_path):
        raise PackagingError("dump not found: %s" % dump_path)
    repair = require_closed_dump(dump_path, repair=repair_dump)
    if repair and verbose:
        print("  repaired truncated dump: discarded %d trailing byte(s)"
              % repair["bytes_discarded"])
    man = load_manifest(manifest_path)
    rows, excluded = load_requests(requests_path, require_success=require_success)
    groups = group_requests(rows, group_by)
    arms = build_arms(groups, group_by)
    if verbose:
        print("  %d usable request row(s), %d excluded, %d arm(s) by %s"
              % (len(rows), len(excluded), len(arms), group_by))

    span = None
    span_verdicts = None
    if span_check:
        if verbose:
            print("  scanning dump span (raw SystemTime bound) ...")
        span = dump_span(dump_path)
        span_verdicts = check_arms_against_span(arms, span, allow_outside=allow_outside_span)
        if verbose:
            print("  dump span %s .. %s (%.3f s, %d timestamps)"
                  % (span["start_utc"], span["end_utc"], span["span_s"], span["n_timestamps"]))

    out_path = os.path.abspath(out_path)
    package_dir = os.path.abspath(package_dir or os.path.dirname(out_path) or ".")
    stem = os.path.splitext(os.path.basename(out_path))[0]
    traces = emit_trace_names(arms, dump_path, package_dir, stem,
                              source_etl=etl_path or man.get("etl"),
                              source_etl_bytes=man.get("etl_bytes"), verbose=verbose)

    report = {
        "generated_by": "etw10_package.py",
        "generated_utc": iso_utc(datetime.now(timezone.utc)),
        "rev": 1,
        "provider": "Microsoft-Windows-DxgKrnl",
        "source": "ring-snapshot",
        "server_pid": man["server_pid"],
        "arms": arms,
        "traces": traces,
        "capture": man,
        "requests": {
            "path": os.path.abspath(requests_path),
            "group_by": group_by,
            "n_rows_used": len(rows),
            "n_excluded": len(excluded),
            "excluded": excluded,
            "require_success": require_success,
        },
        "dump_span": span,
        "arm_span_check": span_verdicts,
        "aggregation_note": (
            "arm prompt_ms/predicted_ms are SUMS over the arm's requests. With "
            "concurrency_observed > 1 the sum exceeds wall_s by construction; read it "
            "beside n_requests and the *_mean fields."),
        "trace_note": (
            "every trace's dump is a NAME for one underlying XML (dump_source), "
            "materialised only because etw2_join/etw4_depth derive the arm label from the "
            "dump filename. N traces here are N WINDOWS on one capture, not N captures."),
    }
    if repair:
        report["dump_repair"] = repair
    if etl_info:
        report["conversion"] = etl_info
        if etl_info.get("truncated"):
            report["truncation_warning"] = (
                "the dump was budget-truncated and repaired to well-formed XML; it holds "
                "the EARLIEST portion of the .etl only. Arms outside dump_span are not in "
                "this trace even though they may be in the .etl.")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(report, indent=2, sort_keys=False))
    return report


def parse_args(argv):
    p = argparse.ArgumentParser(description="package an ETW ring snapshot into report.json")
    p.add_argument("--manifest", help="cap-*.json written beside the .etl by etw6_watch.py")
    p.add_argument("--requests", help="load-harness JSONL (qwen38_campaign.py load rows)")
    p.add_argument("--dump", help="already-converted tracerpt XML (PRIMARY input)")
    p.add_argument("--etl", help="ring snapshot .etl; converted here with tracerpt")
    p.add_argument("--convert-to", help="where a converted dump is written (default: beside --out)")
    p.add_argument("--max-dump-mb", type=int, default=None,
                   help="stop the converter past this many MB and repair the partial XML")
    p.add_argument("--convert-timeout-s", type=float, default=None)
    p.add_argument("--convert-poll-s", type=float, default=5.0,
                   help="how often the converter's output size is checked against the budget")
    p.add_argument("--tracerpt", default="tracerpt")
    p.add_argument("--out", help="report.json to write")
    p.add_argument("--package-dir", help="where the per-arm -rN.dump.xml names are placed")
    p.add_argument("--group-by", default="run", choices=("run", "client"))
    p.add_argument("--keep-failed-requests", action="store_true",
                   help="do not exclude rows marked success=false / valid=false")
    p.add_argument("--no-span-check", action="store_true",
                   help="skip the dump-span scan (and with it the outside-the-trace guard)")
    p.add_argument("--allow-outside-span", action="store_true")
    p.add_argument("--repair-dump", action="store_true",
                   help="a supplied --dump that was cut short: trim to the last complete "
                        "</Event> and close the root, in place")
    p.add_argument("--describe", action="store_true",
                   help="print the span of --dump and/or the groups in --requests; write nothing")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.describe:
        if not args.dump and not args.requests:
            raise PackagingError("--describe needs --dump and/or --requests")
        if args.dump:
            s = dump_span(args.dump)
            print("dump   %s" % s["dump"])
            print("bytes  %d   DxgKrnl timestamps %d of %d"
                  % (s["dump_bytes"], s["n_timestamps"], s["n_timestamps_all_providers"]))
            print("span   %s .. %s  (%.3f s)   <- DxgKrnl events"
                  % (s["start_utc"], s["end_utc"], s["span_s"]))
            print("raw    %s .. %s  (%.3f s)   <- incl. ETW header (session start)"
                  % (s["raw_start_utc"], s["raw_end_utc"], s["raw_span_s"]))
            print("epoch  %.6f .. %.6f" % (s["start_epoch"], s["end_epoch"]))
        if args.requests:
            rows, excluded = load_requests(
                args.requests, require_success=not args.keep_failed_requests)
            for key, group in group_requests(rows, args.group_by):
                lo = min(r["start_epoch"] for r in group)
                hi = max(r["end_epoch"] for r in group)
                print("group  %-28s n=%-4d %s .. %s (%.3f s)"
                      % (key, len(group), iso_utc(datetime.fromtimestamp(lo, timezone.utc)),
                         iso_utc(datetime.fromtimestamp(hi, timezone.utc)), hi - lo))
            print("rows   %d used, %d excluded" % (len(rows), len(excluded)))
        return 0

    for required in ("manifest", "requests", "out"):
        if not getattr(args, required):
            raise PackagingError("--%s is required" % required)
    if bool(args.dump) == bool(args.etl):
        raise PackagingError("pass exactly one of --dump or --etl")

    etl_info = None
    dump = args.dump
    if args.etl:
        dump = args.convert_to or os.path.join(
            os.path.dirname(os.path.abspath(args.out)) or ".",
            os.path.splitext(os.path.basename(args.etl))[0] + ".dump.xml")
        etl_info = convert_etl(args.etl, dump, max_dump_mb=args.max_dump_mb,
                               tracerpt_exe=args.tracerpt, poll_s=args.convert_poll_s,
                               timeout_s=args.convert_timeout_s)
        etl_info["etl"] = os.path.abspath(args.etl)

    rep = build_report(args.manifest, args.requests, dump, args.out,
                       group_by=args.group_by, package_dir=args.package_dir,
                       etl_path=os.path.abspath(args.etl) if args.etl else None,
                       etl_info=etl_info,
                       require_success=not args.keep_failed_requests,
                       span_check=not args.no_span_check,
                       allow_outside_span=args.allow_outside_span,
                       repair_dump=args.repair_dump)
    print("\nreport: %s" % os.path.abspath(args.out))
    print("  server_pid %s   arms %d   traces %d"
          % (rep["server_pid"], len(rep["arms"]), len(rep["traces"])))
    for a in rep["arms"]:
        print("  %-8s %-24s n=%-4d wall=%8.3f s  prompt_ms=%10.2f predicted_ms=%10.2f  conc=%d"
              % (a["label"], a["group_key"][:24], a["n_requests"], a["wall_s"],
                 a["prompt_ms"], a["predicted_ms"], a["concurrency_observed"]))
    print("\n  next: python etw4_depth.py %s" % os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PackagingError as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        sys.exit(2)
