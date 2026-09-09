"""Fixtures for ETW10, the ring-snapshot -> report.json packager.

The failure modes this campaign actually hit are the ones under test: a join that returns
EMPTY instead of raising (etw2_join's two regression fixtures), an epoch offset by 28800 s
that produced a plausible empty window, and a queue statistic computed over a window with
no events. Every one of those is a silent wrong answer, so the packager is required to
REFUSE, and these fixtures prove it refuses rather than merely documenting that it should.

Run: fleet-worker-node\\.venv-omen\\Scripts\\python.exe -m unittest discover -s campaign/lz-probes -p "test_etw10_package.py"
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etw10_package as pkg  # noqa: E402
from etw2_join import epoch, events  # noqa: E402

DXGK_NS = "http://schemas.microsoft.com/win/2004/08/events/event"


def dma_event(eid, ts, pid, fence, hwqueue):
    """One DmaPacket/Info event in the exact shape tracerpt emits (verified against
    E:\\work\\battlemage\\ff-probes\\etw-20260830\\etw1-20260830-043447-r1.dump.xml)."""
    return (
        '<Event xmlns="%s">\n'
        "\t<System>\n"
        '\t\t<Provider Name="Microsoft-Windows-DxgKrnl" Guid="{802ec45a-1e99-4b83-9920-87c98277ba9d}" />\n'
        "\t\t<EventID>%s</EventID>\n"
        '\t\t<TimeCreated SystemTime="%s" />\n'
        '\t\t<Execution ProcessID="%s" ThreadID="1832" ProcessorID="1" />\n'
        "\t</System>\n"
        "\t<EventData>\n"
        '\t\t<Data Name="ProgressFenceValue">%s</Data>\n'
        '\t\t<Data Name="hHwQueue">%s</Data>\n'
        "\t</EventData>\n"
        '\t<RenderingInfo Culture="en-US">\n'
        "\t\t<Opcode>Info </Opcode>\n"
        "\t\t<Task>DmaPacket</Task>\n"
        "\t</RenderingInfo>\n"
        "</Event>\n" % (DXGK_NS, eid, ts, pid, fence, hwqueue)
    )


def write_dump(path, base_epoch=1788118480.0, n=40, pid=20416, hwqueue="0xFFFFBB041C43A150"):
    """A small but structurally real dump: n submit/complete pairs on one hw queue."""
    import datetime as dt

    def iso(offset):
        return dt.datetime.fromtimestamp(base_epoch + offset, dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f") + "00-00:00"

    parts = ["<Events>\n"]
    for i in range(n):
        parts.append(dma_event(450, iso(i * 0.010), pid, 1000 + i, hwqueue))
        parts.append(dma_event(451, iso(i * 0.010 + 0.007), 0, 1000 + i, hwqueue))
    parts.append("</Events>\n")
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(parts))
    return path


def manifest(**over):
    man = {
        "tag": "arc",
        "captured_utc": "2026-09-01T15:24:09.604794+00:00",
        "etl": r"E:\work\battlemage\ff-probes\etw-recorder\captures\cap-20260901-152356-arc.etl",
        "etl_bytes": 19327352832,
        "server_pid": 20416,
        "etw_keywords": "0x4000000000000001",
        "deep_compute_queues": {"values": ["0xFFFFBB041C43A150", "0xFFFFBB03C9FACB40"]},
    }
    man.update(over)
    return man


def row(**over):
    r = {
        "contract_version": "qwen38-request.v1",
        "run_id": "run-A",
        "client_id": 0,
        "started_at": "2026-09-01T15:20:00.000000Z",
        "completed_at": "2026-09-01T15:20:00.400000Z",
        "prompt_ms": 10.4,
        "predicted_ms": 305.1,
        "success": True,
        "valid": True,
        "predicted_tokens": 32,
    }
    r.update(over)
    return r


def write_jsonl(path, rows):
    with io.open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return path


class TraceLabelRuleTests(unittest.TestCase):
    """The readers derive the arm label from the dump FILENAME. It is the join key."""

    READERS_RULE = staticmethod(lambda dump: "etw-" + dump.split("-r")[-1].split(".")[0])

    def test_matches_the_readers_expression_verbatim(self) -> None:
        for path in (
            r"E:\work\battlemage\ff-probes\etw-20260830\etw1-20260830-043447-r1.dump.xml",
            r"E:\work\battlemage\ff-probes\etw-20260830\etw1-20260830-043447-r2.dump.xml",
            r"E:\pkg\report-r10.dump.xml",
            r"E:\work\battlemage\ff-probes\etw-recorder\captures\cap-20260901-152356-arc.dump.xml",
            "plain.xml",
        ):
            self.assertEqual(self.READERS_RULE(path), pkg.trace_label(path), path)

    def test_historic_producer_names_resolve_to_etw_N(self) -> None:
        self.assertEqual(
            "etw-1",
            pkg.trace_label(r"E:\work\battlemage\ff-probes\etw-20260830\etw1-20260830-043447-r1.dump.xml"))
        self.assertEqual(
            "etw-2",
            pkg.trace_label(r"E:\work\battlemage\ff-probes\etw-20260830\etw1-20260830-043447-r2.dump.xml"))

    def test_a_directory_named_etw_recorder_poisons_a_bare_capture_name(self) -> None:
        """Why every emitted name ends in -rN: the rule splits on the LAST '-r' in the
        WHOLE PATH, so etw-recorder\\ in the directory becomes the join key when the
        filename contributes no '-r' of its own."""
        bad = r"E:\work\battlemage\ff-probes\etw-recorder\captures\cap-20260901-152356-arc.dump.xml"
        self.assertNotEqual("etw-1", pkg.trace_label(bad))
        self.assertTrue(pkg.trace_label(bad).startswith("etw-ecorder"))
        good = os.path.join(os.path.dirname(bad), pkg.package_dump_name("cap-20260901-152356-arc", 1))
        self.assertEqual("etw-1", pkg.trace_label(good))

    def test_package_names_round_trip_for_every_arm_index(self) -> None:
        for i in range(1, 13):
            name = pkg.package_dump_name("report", i)
            self.assertEqual(pkg.arm_label(i), pkg.trace_label(os.path.join(r"C:\some-run\dir", name)))

    def test_emit_trace_names_refuses_a_name_that_does_not_derive_its_arm_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dump = write_dump(os.path.join(tmp, "src.dump.xml"), n=2)
            arms = [{"label": "etw-9", "wall_s": 1.0}]   # deliberately mismatched
            with self.assertRaises(pkg.PackagingError) as ctx:
                pkg.emit_trace_names(arms, dump, tmp, "report", verbose=False)
            self.assertIn("derives label", str(ctx.exception))

    def test_emit_trace_names_materialises_one_name_per_arm_over_one_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dump = write_dump(os.path.join(tmp, "src.dump.xml"), n=3)
            arms = [{"label": "etw-1", "wall_s": 1.0}, {"label": "etw-2", "wall_s": 1.0}]
            traces = pkg.emit_trace_names(arms, dump, tmp, "report", verbose=False)
            self.assertEqual(2, len(traces))
            self.assertEqual(["etw-1", "etw-2"], [pkg.trace_label(t["dump"]) for t in traces])
            self.assertEqual({os.path.abspath(dump)}, {t["dump_source"] for t in traces})
            for t in traces:
                self.assertTrue(os.path.exists(t["dump"]))
                self.assertEqual(os.path.getsize(dump), t["dump_bytes"])

    def test_a_stale_name_of_equal_size_is_relinked_not_reused(self) -> None:
        """Identity, not a size coincidence: a leftover name pointing at a DIFFERENT
        capture would silently mislabel the trace."""
        with tempfile.TemporaryDirectory() as tmp:
            src = write_dump(os.path.join(tmp, "src.dump.xml"), n=4, hwqueue="0xAAAA")
            stale = write_dump(os.path.join(tmp, "report-r1.dump.xml"), n=4, hwqueue="0xBBBB")
            self.assertEqual(os.path.getsize(src), os.path.getsize(stale))
            traces = pkg.emit_trace_names([{"label": "etw-1", "wall_s": 1.0}], src, tmp,
                                          "report", verbose=False)
            self.assertIn(traces[0]["dump_link"], ("hardlink", "copy"))
            with io.open(traces[0]["dump"], encoding="utf-8") as fh:
                self.assertIn("0xAAAA", fh.read())


class ManifestTests(unittest.TestCase):
    def test_parses_a_real_capture_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "cap.json")
            with io.open(p, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(manifest()))
            man = pkg.load_manifest(p)
            self.assertEqual(20416, man["server_pid"])
            self.assertEqual("arc", man["tag"])
            self.assertEqual(19327352832, man["etl_bytes"])
            self.assertEqual(["0xFFFFBB041C43A150", "0xFFFFBB03C9FACB40"],
                             man["deep_compute_queues"]["values"])

    def test_reads_a_utf8_bom_manifest(self) -> None:
        """etw6_session.ps1 writes JSON with Set-Content -Encoding utf8, which emits a BOM
        in PS 5.1; the readers all open with utf-8-sig for that reason."""
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "cap.json")
            with io.open(p, "w", encoding="utf-8-sig") as fh:
                fh.write(json.dumps(manifest()))
            self.assertEqual(20416, pkg.load_manifest(p)["server_pid"])

    def test_missing_server_pid_is_refused_not_defaulted(self) -> None:
        """etw4_depth does str(rep.get('server_pid')); a None becomes 'None', matches no
        event, and reports server_share 0% on every queue instead of failing."""
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "cap.json")
            with io.open(p, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(manifest(server_pid=None)))
            with self.assertRaises(pkg.PackagingError) as ctx:
                pkg.load_manifest(p)
            self.assertIn("server_pid", str(ctx.exception))

    def test_non_integer_server_pid_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "cap.json")
            with io.open(p, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(manifest(server_pid="not-a-pid")))
            self.assertRaises(pkg.PackagingError, pkg.load_manifest, p)

    def test_missing_file_is_refused(self) -> None:
        self.assertRaises(pkg.PackagingError, pkg.load_manifest, r"C:\nope\cap.json")


class MalformedRowTests(unittest.TestCase):
    """A malformed row is rejected LOUDLY. It is never skipped."""

    def _reject(self, rows, needle):
        with tempfile.TemporaryDirectory() as tmp:
            p = write_jsonl(os.path.join(tmp, "rows.jsonl"), rows)
            with self.assertRaises(pkg.MalformedRequestRow) as ctx:
                pkg.load_requests(p)
            self.assertIn(needle, str(ctx.exception))
            return ctx.exception

    def test_unparseable_json_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "rows.jsonl")
            with io.open(p, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(row()) + "\n{not json\n")
            with self.assertRaises(pkg.MalformedRequestRow) as ctx:
                pkg.load_requests(p)
            self.assertEqual(2, ctx.exception.lineno)

    def test_missing_required_field(self) -> None:
        for field in pkg.REQUIRED_ROW_FIELDS:
            bad = row()
            bad.pop(field)
            self._reject([row(), bad], field)

    def test_completed_before_started(self) -> None:
        self._reject([row(started_at="2026-09-01T15:20:05.000000Z",
                          completed_at="2026-09-01T15:20:00.000000Z")], "precedes")

    def test_unparseable_timestamp(self) -> None:
        self._reject([row(completed_at="not-a-timestamp")], "completed_at")

    def test_non_numeric_timing(self) -> None:
        self._reject([row(prompt_ms="10.4")], "not a number")

    def test_null_timings_on_a_row_not_marked_failed(self) -> None:
        exc = self._reject([row(prompt_ms=None)], "incoherent")
        self.assertEqual(1, exc.lineno)

    def test_empty_run_id(self) -> None:
        self._reject([row(run_id="")], "run_id is empty")

    def test_a_log_with_no_usable_rows_is_refused_not_packaged_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = write_jsonl(os.path.join(tmp, "rows.jsonl"),
                            [row(success=False, prompt_ms=None, predicted_ms=None)])
            with self.assertRaises(pkg.PackagingError) as ctx:
                pkg.load_requests(p)
            self.assertIn("no usable rows", str(ctx.exception))

    def test_failed_rows_are_excluded_but_counted_never_silent(self) -> None:
        rows = [row(),
                row(started_at="2026-09-01T15:20:01.000000Z",
                    completed_at="2026-09-01T15:20:01.002000Z",
                    success=False, prompt_ms=None, predicted_ms=None),
                row(started_at="2026-09-01T15:20:02.000000Z",
                    completed_at="2026-09-01T15:20:02.400000Z",
                    valid=False, failure_class="short_generation")]
        with tempfile.TemporaryDirectory() as tmp:
            p = write_jsonl(os.path.join(tmp, "rows.jsonl"), rows)
            used, excluded = pkg.load_requests(p)
            self.assertEqual(1, len(used))
            self.assertEqual(2, len(excluded))
            self.assertEqual([2, 3], [e["line"] for e in excluded])
            self.assertIn("success=false", excluded[0]["reason"])
            self.assertIn("short_generation", excluded[1]["reason"])

    def test_keep_failed_requests_includes_them_when_asked(self) -> None:
        rows = [row(), row(started_at="2026-09-01T15:20:02.000000Z",
                           completed_at="2026-09-01T15:20:02.400000Z",
                           valid=False, failure_class="short_generation")]
        with tempfile.TemporaryDirectory() as tmp:
            p = write_jsonl(os.path.join(tmp, "rows.jsonl"), rows)
            used, excluded = pkg.load_requests(p, require_success=False)
            self.assertEqual(2, len(used))
            self.assertEqual([], excluded)


class ArmDerivationTests(unittest.TestCase):
    FIXTURE = [
        # run-A, two clients, overlapping -> one arm by run, two by client
        row(run_id="run-A", client_id=0,
            started_at="2026-09-01T15:20:00.000000Z", completed_at="2026-09-01T15:20:00.400000Z",
            prompt_ms=10.0, predicted_ms=300.0, predicted_tokens=32),
        row(run_id="run-A", client_id=1,
            started_at="2026-09-01T15:20:00.100000Z", completed_at="2026-09-01T15:20:00.500000Z",
            prompt_ms=12.0, predicted_ms=320.0, predicted_tokens=32),
        row(run_id="run-A", client_id=0,
            started_at="2026-09-01T15:20:00.400000Z", completed_at="2026-09-01T15:20:00.700000Z",
            prompt_ms=11.0, predicted_ms=280.0, predicted_tokens=32),
        # run-B, later, single client
        row(run_id="run-B", client_id=0,
            started_at="2026-09-01T15:21:00.000000Z", completed_at="2026-09-01T15:21:00.500000Z",
            prompt_ms=40.0, predicted_ms=600.0, predicted_tokens=32),
    ]

    def _arms(self, group_by):
        with tempfile.TemporaryDirectory() as tmp:
            p = write_jsonl(os.path.join(tmp, "rows.jsonl"), self.FIXTURE)
            rows, _ = pkg.load_requests(p)
        return pkg.build_arms(pkg.group_requests(rows, group_by), group_by)

    def test_one_arm_per_run_labelled_in_start_order(self) -> None:
        arms = self._arms("run")
        self.assertEqual(["etw-1", "etw-2"], [a["label"] for a in arms])
        self.assertEqual(["run-A", "run-B"], [a["group_key"] for a in arms])

    def test_window_is_earliest_start_to_latest_completion(self) -> None:
        a = self._arms("run")[0]
        self.assertEqual("2026-09-01T15:20:00.000000Z", a["start_utc"])
        self.assertEqual("2026-09-01T15:20:00.700000Z", a["end_utc"])
        self.assertAlmostEqual(0.7, a["wall_s"], places=6)
        self.assertEqual(3, a["n_requests"])

    def test_epochs_survive_the_readers_own_parser(self) -> None:
        """The 28800 s bug: PS 5.1 parsed '1970-01-01Z' as LOCAL and the join went EMPTY
        instead of failing. Every emitted string is re-read through etw2_join.epoch."""
        for a in self._arms("run"):
            self.assertAlmostEqual(a["start_epoch"], epoch(a["start_utc"]), places=2)
            self.assertAlmostEqual(a["end_epoch"], epoch(a["end_utc"]), places=2)
            self.assertAlmostEqual(a["end_epoch"] - a["start_epoch"], a["wall_s"], places=2)

    def test_timings_are_summed_and_the_mean_is_carried_alongside(self) -> None:
        a = self._arms("run")[0]
        self.assertAlmostEqual(33.0, a["prompt_ms"], places=4)
        self.assertAlmostEqual(900.0, a["predicted_ms"], places=4)
        self.assertEqual(a["prompt_ms"], a["prompt_ms_sum"])
        self.assertAlmostEqual(11.0, a["prompt_ms_mean"], places=4)
        self.assertAlmostEqual(300.0, a["predicted_ms_mean"], places=4)
        self.assertEqual(96, a["predicted_n"])

    def test_concurrency_is_reported_because_the_sum_exceeds_wall_time(self) -> None:
        a = self._arms("run")[0]
        self.assertEqual(2, a["concurrency_observed"])
        self.assertGreater(a["predicted_ms"] / 1000.0, a["wall_s"])

    def test_back_to_back_requests_are_not_counted_as_concurrent(self) -> None:
        """c0's two requests abut at 15:20:00.400. Counting the shared instant as overlap
        would report concurrency 2 on a strictly sequential client."""
        self.assertEqual(1, self._arms("client")[0]["concurrency_observed"])

    def test_group_by_client_splits_the_same_run(self) -> None:
        arms = self._arms("client")
        self.assertEqual(["run-A/c0", "run-A/c1", "run-B/c0"], [a["group_key"] for a in arms])
        self.assertEqual(["etw-1", "etw-2", "etw-3"], [a["label"] for a in arms])
        self.assertEqual(1, arms[0]["concurrency_observed"])
        self.assertEqual(2, arms[0]["n_requests"])

    def test_unknown_group_by_is_refused(self) -> None:
        self.assertRaises(pkg.PackagingError, pkg.group_requests, [], "queue")


HEADER_EVENT = (
    '<Event xmlns="%s">\n'
    "\t<System>\n"
    '\t\t<Provider Guid="{9e814aad-3204-11d2-9a82-006008a86939}" />\n'
    "\t\t<EventID>0</EventID>\n"
    '\t\t<TimeCreated SystemTime="%%s" />\n'
    '\t\t<Execution ProcessID="15392" ThreadID="1" ProcessorID="0" />\n'
    "\t</System>\n"
    "\t<EventData>\n"
    '\t\t<Data Name="LoggerName">0xA</Data>\n'
    "\t</EventData>\n"
    "</Event>\n" % DXGK_NS
)


class DumpSpanTests(unittest.TestCase):
    def test_span_bounds_the_events_in_the_dump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = write_dump(os.path.join(tmp, "d.dump.xml"), base_epoch=1788118480.0, n=10)
            span = pkg.dump_span(d)
            self.assertEqual(20, span["n_timestamps"])
            self.assertAlmostEqual(1788118480.0, span["start_epoch"], places=3)
            self.assertAlmostEqual(1788118480.097, span["end_epoch"], places=3)

    def test_the_etw_header_session_start_does_not_inflate_the_span(self) -> None:
        """The failure this guards, measured on cap-20260901-152356-arc: every SystemTime
        gives a 178,646 s bound; DxgKrnl events alone give 164 s. The inflated bound would
        license an arm anywhere in two days against a three-minute trace."""
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "d.dump.xml")
            body = write_dump(os.path.join(tmp, "body.xml"), base_epoch=1788273327.0, n=5)
            with io.open(body, encoding="utf-8") as fh:
                inner = fh.read()[len("<Events>\n"):-len("</Events>\n")]
            # session start two days before the oldest retained event, as a wrapped ring does
            with io.open(d, "w", encoding="utf-8") as fh:
                fh.write("<Events>\n")
                fh.write(HEADER_EVENT % "2026-08-30T13:00:45.299686900+00:00")
                fh.write(inner)
                fh.write("</Events>\n")
            span = pkg.dump_span(d)
            self.assertEqual(10, span["n_timestamps"])
            self.assertEqual(11, span["n_timestamps_all_providers"])
            self.assertAlmostEqual(1788273327.0, span["start_epoch"], places=3)
            self.assertLess(span["span_s"], 1.0)
            self.assertGreater(span["raw_span_s"], 100000.0)

    def test_a_dump_of_header_events_only_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "hdr.dump.xml")
            with io.open(p, "w", encoding="utf-8") as fh:
                fh.write("<Events>\n")
                fh.write(HEADER_EVENT % "2026-08-30T13:00:45.299686900+00:00")
                fh.write("</Events>\n")
            with self.assertRaises(pkg.PackagingError) as ctx:
                pkg.dump_span(p)
            self.assertIn("DxgKrnl", str(ctx.exception))

    def test_a_non_dump_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "empty.xml")
            with io.open(p, "w", encoding="utf-8") as fh:
                fh.write("<Events>\n</Events>\n")
            self.assertRaises(pkg.PackagingError, pkg.dump_span, p)

    def test_an_arm_outside_the_dump_is_refused(self) -> None:
        span = {"start_epoch": 100.0, "end_epoch": 200.0,
                "start_utc": "a", "end_utc": "b"}
        arms = [{"label": "etw-1", "start_epoch": 300.0, "end_epoch": 400.0, "wall_s": 100.0}]
        with self.assertRaises(pkg.PackagingError) as ctx:
            pkg.check_arms_against_span(arms, span)
        self.assertIn("entirely outside", str(ctx.exception))

    def test_allow_outside_span_records_it_instead_of_refusing(self) -> None:
        span = {"start_epoch": 100.0, "end_epoch": 200.0, "start_utc": "a", "end_utc": "b"}
        arms = [{"label": "etw-1", "start_epoch": 300.0, "end_epoch": 400.0, "wall_s": 100.0}]
        v = pkg.check_arms_against_span(arms, span, allow_outside=True)
        self.assertFalse(v[0]["intersects_dump"])
        self.assertEqual(0.0, v[0]["overlap_s"])

    def test_partial_overlap_is_reported_as_a_fraction(self) -> None:
        span = {"start_epoch": 100.0, "end_epoch": 200.0, "start_utc": "a", "end_utc": "b"}
        arms = [{"label": "etw-1", "start_epoch": 150.0, "end_epoch": 250.0, "wall_s": 100.0}]
        v = pkg.check_arms_against_span(arms, span)
        self.assertTrue(v[0]["intersects_dump"])
        self.assertAlmostEqual(0.5, v[0]["covered_fraction"], places=4)


class TruncatedDumpRepairTests(unittest.TestCase):
    def test_a_killed_conversion_is_repaired_to_well_formed_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = write_dump(os.path.join(tmp, "full.dump.xml"), n=20)
            with io.open(good, "rb") as fh:
                raw = fh.read()
            cut = raw.find(b"</Event>", len(raw) // 2) + 400   # mid-event truncation
            broken = os.path.join(tmp, "part.dump.xml")
            with io.open(broken, "wb") as fh:
                fh.write(raw[:cut])
            self.assertRaises(ET.ParseError, ET.parse, broken)

            info = pkg.repair_truncated_xml(broken)
            self.assertTrue(info["repaired"])
            self.assertGreater(info["bytes_discarded"], 0)
            root = ET.parse(broken).getroot()
            self.assertEqual("Events", root.tag)
            self.assertGreater(len(root), 0)

    def test_repaired_dump_still_yields_events_to_the_readers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = write_dump(os.path.join(tmp, "full.dump.xml"), n=20)
            with io.open(good, "rb") as fh:
                raw = fh.read()
            broken = os.path.join(tmp, "part.dump.xml")
            with io.open(broken, "wb") as fh:
                fh.write(raw[:raw.find(b"</Event>", len(raw) // 2) + 300])
            pkg.repair_truncated_xml(broken)
            got = [e for e in events(broken) if e[0] == "DmaPacket"]
            self.assertGreater(len(got), 0)
            rtask, opcode, eid, pid, ts, data = got[0]
            self.assertEqual(("DmaPacket", "Info", "450"), (rtask, opcode, eid))
            self.assertEqual("0xFFFFBB041C43A150", data["hHwQueue"])
            self.assertIsNotNone(ts)

    def test_a_truncated_dump_is_refused_before_the_reader_sees_it(self) -> None:
        """iterparse streams most of a cut dump and only THEN raises, after a partial
        analysis has already printed. The tail check fires first."""
        with tempfile.TemporaryDirectory() as tmp:
            good = write_dump(os.path.join(tmp, "full.dump.xml"), n=20)
            with io.open(good, "rb") as fh:
                raw = fh.read()
            broken = os.path.join(tmp, "part.dump.xml")
            with io.open(broken, "wb") as fh:
                fh.write(raw[:raw.find(b"</Event>", len(raw) // 2) + 300])
            self.assertTrue(pkg.dump_is_closed(good))
            self.assertFalse(pkg.dump_is_closed(broken))
            with self.assertRaises(pkg.PackagingError) as ctx:
                pkg.require_closed_dump(broken)
            self.assertIn("--repair-dump", str(ctx.exception))
            info = pkg.require_closed_dump(broken, repair=True)
            self.assertTrue(info["repaired"])
            self.assertTrue(pkg.dump_is_closed(broken))
            self.assertIsNone(pkg.require_closed_dump(broken))

    def test_a_dump_with_no_complete_event_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "stub.dump.xml")
            with io.open(p, "wb") as fh:
                fh.write(b"<Events>\n<Event xmlns='x'><System>")
            self.assertRaises(pkg.PackagingError, pkg.repair_truncated_xml, p)

    def test_repair_does_not_mistake_the_root_close_for_an_event_close(self) -> None:
        """</Event> is a prefix of </Events>."""
        with tempfile.TemporaryDirectory() as tmp:
            p = write_dump(os.path.join(tmp, "full.dump.xml"), n=3)
            with io.open(p, "rb") as fh:
                before = fh.read()
            info = pkg.repair_truncated_xml(p)
            with io.open(p, "rb") as fh:
                after = fh.read()
            self.assertEqual(before.count(b"</Event>") - before.count(b"</Events>"),
                             after.count(b"</Event>") - after.count(b"</Events>"))
            self.assertEqual(1, after.count(b"</Events>"))
            self.assertGreater(info["bytes_after"], 0)


class EndToEndReportTests(unittest.TestCase):
    def _package(self, tmp, group_by="run", rows=None):
        base = 1788118480.0
        dump = write_dump(os.path.join(tmp, "src.dump.xml"), base_epoch=base, n=60)
        man = os.path.join(tmp, "cap.json")
        with io.open(man, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest()))
        import datetime as dt

        def iso(off):
            return dt.datetime.fromtimestamp(base + off, dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f") + "Z"
        rows = rows or [
            row(run_id="run-A", client_id=0, started_at=iso(0.00), completed_at=iso(0.20)),
            row(run_id="run-A", client_id=1, started_at=iso(0.05), completed_at=iso(0.25)),
            row(run_id="run-B", client_id=0, started_at=iso(0.30), completed_at=iso(0.55)),
        ]
        req = write_jsonl(os.path.join(tmp, "rows.jsonl"), rows)
        out = os.path.join(tmp, "report.json")
        rep = pkg.build_report(man, req, dump, out, group_by=group_by, verbose=False)
        return rep, out

    def test_report_carries_the_keys_the_readers_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rep, out = self._package(tmp)
            self.assertEqual(20416, rep["server_pid"])
            for arm in rep["arms"]:
                for key in ("label", "start_utc", "end_utc", "prompt_ms", "predicted_ms"):
                    self.assertIn(key, arm)
            for tr in rep["traces"]:
                self.assertIn("dump", tr)
                self.assertTrue(os.path.exists(tr["dump"]))
            with io.open(out, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["server_pid"], 20416)

    def test_every_trace_resolves_to_an_arm_by_the_readers_own_rule(self) -> None:
        """etw4_depth.main does arms[lab] and KeyErrors if the label is absent."""
        with tempfile.TemporaryDirectory() as tmp:
            rep, _ = self._package(tmp)
            arms = {a["label"]: a for a in rep["arms"]}
            self.assertEqual(len(rep["arms"]), len(rep["traces"]))
            for tr in rep["traces"]:
                lab = "etw-" + tr["dump"].split("-r")[-1].split(".")[0]
                self.assertIn(lab, arms)
                self.assertEqual(tr["arm"], lab)

    def test_the_readers_epoch_derivation_lands_inside_the_dump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rep, _ = self._package(tmp)
            span = rep["dump_span"]
            for a in rep["arms"]:
                se, ee = epoch(a["start_utc"]), epoch(a["end_utc"])
                self.assertIsNotNone(se)
                self.assertIsNotNone(ee)
                self.assertGreaterEqual(se, span["start_epoch"] - 0.001)
                self.assertLessEqual(ee, span["end_epoch"] + 0.001)

    def test_traces_declare_that_they_share_one_underlying_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rep, _ = self._package(tmp)
            self.assertEqual(1, len({t["dump_source"] for t in rep["traces"]}))
            self.assertEqual(2, len({t["dump"] for t in rep["traces"]}))
            self.assertIn("not N captures", rep["trace_note"])

    def test_excluded_rows_reach_the_report(self) -> None:
        base = 1788118480.0
        import datetime as dt

        def iso(off):
            return dt.datetime.fromtimestamp(base + off, dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f") + "Z"
        with tempfile.TemporaryDirectory() as tmp:
            rep, _ = self._package(tmp, rows=[
                row(run_id="run-A", started_at=iso(0.0), completed_at=iso(0.2)),
                row(run_id="run-A", started_at=iso(0.3), completed_at=iso(0.31),
                    success=False, prompt_ms=None, predicted_ms=None),
            ])
            self.assertEqual(1, rep["requests"]["n_rows_used"])
            self.assertEqual(1, rep["requests"]["n_excluded"])
            self.assertEqual(2, rep["requests"]["excluded"][0]["line"])


if __name__ == "__main__":
    unittest.main()
