"""Research-seat harvest (ADR-0047) on synthetic logs built from the real line shapes.

No real model path, label or port appears here beyond the research-seat ports the
eligibility rule names; the model is ``secret-model`` so a leak into a receipt is
an assertion failure, not a review finding.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hearth.execution.ledger import _lock_file, _unlock_file
from hearth.seats import harvest as harvest_mod
from hearth.seats import serverlog
from hearth.seats.harvest import (
    QUIESCENT_S, SKIP_DOOR_REACHABLE, SKIP_NO_LISTEN, SKIP_PORT, SKIP_SHORT, harvest,
)
from hearth.seats.receipts import (
    SCHEMA, SeatReceiptError, SeatReceiptsLedger, validate_receipt,
)

NOW = datetime(2026, 9, 19, 8, 0, 0, tzinfo=timezone.utc)
MTIME = 1789800000.0  # an arbitrary fixed epoch second, well before NOW


def _stamp(seconds: float) -> str:
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    whole = int(rest)
    millis = int(round((rest - whole) * 1000))
    return f"{minutes}.{whole:02d}.{millis:03d}.000"


def _header(port: int | None = 8096, api_key: bool = False, stamped: int = 70,
            filler_offset: int = 0, build: bool = True, model: bool = True,
            n_ctx_seq: bool = True, n_ctx_slot: bool = False) -> list[str]:
    lines = []
    if build:
        lines.append("0.00.563.501 I cmn  common_param: common_params_print_info: build 52 (60cdd25) "
                     "with MSVC 19.44.35228.0 for Windows AMD64")
    lines.append("0.00.563.502 I cmn  common_param: common_params_print_info: verbosity = 5")
    if api_key:
        lines.append("0.00.600.000 I srv          init: api_keys: ****abcd")
    if model:
        lines.append("0.00.726.633 I llama_model_loader: - kv   5:                               "
                     "general.name str              = secret-model")
    if n_ctx_seq:
        lines.append("0.00.939.269 I llama_context: n_ctx_seq             = 131072")
    if n_ctx_slot:
        lines.append("0.08.236.471 I srv    load_model: initializing, n_slots = 2, n_ctx_slot = 65536, kv_unified = 'false'")
    while len(lines) < stamped:
        i = len(lines) + filler_offset
        lines.append(f"0.01.{i % 1000:03d}.{(i // 1000) % 1000:03d} D srv          init: serve nocache for file{i}")
    if port is not None:
        lines.append(f"0.08.892.941 I srv  llama_server: listening on http://127.0.0.1:{port}")
    return lines


def _task(slot: int, task: int, at_s: float, prompt_n: int = 100, predicted_n: int = 8,
          prompt_ms: float = 120.5, eval_ms: float = 40.25, timed: bool = True,
          release: bool = True) -> list[str]:
    lines = [f"{_stamp(at_s)} I slot launch_slot_: id  {slot} | task {task} | processing task, is_child = 0"]
    if timed:
        lines.append(f"{_stamp(at_s + 1)} I slot print_timing: id  {slot} | task {task} | prompt processing, "
                     "n_tokens =   2048, progress = 0.13, t =   3.03 s / 676.93 tokens per second")
        lines.append(f"{_stamp(at_s + 2)} I slot print_timing: id  {slot} | task {task} | prompt eval time = "
                     f"{prompt_ms:9.2f} ms / {prompt_n:5d} tokens (    1.23 ms per token,   812.86 tokens per second)")
        lines.append(f"{_stamp(at_s + 2)} I slot print_timing: id  {slot} | task {task} |        eval time = "
                     f"{eval_ms:9.2f} ms / {predicted_n:5d} tokens (   71.25 ms per token,    14.04 tokens per second)")
        lines.append(f"{_stamp(at_s + 2)} I slot print_timing: id  {slot} | task {task} |       total time = "
                     "23969.25 ms / 15899 tokens")
    if release:
        n_tokens = prompt_n + predicted_n if timed else 0
        lines.append(f"{_stamp(at_s + 3)} I slot      release: id  {slot} | task {task} | stop processing: "
                     f"n_tokens = {n_tokens}, truncated = 0")
    return lines


def _write_log(path: Path, lines: list[str], mtime: float = MTIME) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Workspace:
    def __init__(self, root: Path) -> None:
        self.logs = root / "swap-logs"
        self.logs.mkdir()
        self.ledger = root / "seats" / "receipts.ndjson"
        self.cursor = root / "seats" / "harvest-cursor.json"

    def run(self, **kwargs):
        kwargs.setdefault("now", NOW)
        return harvest(self.logs, self.ledger, self.cursor, **kwargs)

    def rows(self) -> list[dict]:
        return list(SeatReceiptsLedger(self.ledger).rows())


class ServerLogParserTest(unittest.TestCase):
    def test_parser_pairs_launch_timing_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "seat-8096.log"
            _write_log(log, _header() + _task(0, 1, 100.0, prompt_n=15835, predicted_n=96)
                       + _task(1, 2, 200.0, prompt_n=7, predicted_n=3, prompt_ms=9.5, eval_ms=1.25))
            result = serverlog.scan(log)
            self.assertEqual(result.build, "build 52 (60cdd25)")
            self.assertEqual(result.model_name, "secret-model")
            self.assertEqual(result.n_ctx_seq, 131072)
            self.assertEqual(result.port, 8096)
            self.assertFalse(result.api_keyed)
            self.assertIsNotNone(result.prefix_sha256)
            self.assertEqual(sorted(result.tasks), [(0, 1), (1, 2)])
            first = result.tasks[(0, 1)]
            self.assertEqual((first.prompt_n, first.predicted_n), (15835, 96))
            self.assertAlmostEqual(first.prompt_ms, 120.5)
            self.assertAlmostEqual(first.eval_ms, 40.25)
            self.assertAlmostEqual(first.launched_s, 100.0)
            self.assertAlmostEqual(first.timed_s, 102.0)
            self.assertAlmostEqual(first.released_s, 103.0)
            self.assertTrue(first.timed)
            second = result.tasks[(1, 2)]
            self.assertEqual((second.prompt_n, second.predicted_n), (7, 3))
            self.assertAlmostEqual(second.last_elapsed_s if hasattr(second, "last_elapsed_s") else result.last_elapsed_s, 203.0)

    def test_elapsed_stamp_minutes_are_unbounded(self) -> None:
        match = serverlog.ELAPSED_RE.match("3731.26.818.590 I slot print_timing: id 0 | task 9 | x")
        self.assertIsNotNone(match)
        self.assertAlmostEqual(serverlog.elapsed_seconds(match), 3731 * 60 + 26 + 0.818590, places=6)

    def test_epoch_start_is_mtime_minus_last_stamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "seat-8096.log"
            _write_log(log, _header() + _task(0, 1, 100.0), mtime=MTIME)
            result = serverlog.scan(log)
            # The last stamped line is the release at 103.0 s.
            self.assertAlmostEqual(result.last_elapsed_s, 103.0)
            epoch = serverlog.epoch_start(result)
            expected = datetime.fromtimestamp(MTIME, tz=timezone.utc) - timedelta(seconds=103.0)
            self.assertEqual(epoch, expected)
            finished = serverlog.rfc3339(serverlog.wall_clock(epoch, 102.0))
            self.assertTrue(finished.endswith("Z"))
            self.assertEqual(finished, serverlog.rfc3339(expected + timedelta(seconds=102.0)))


class HarvestTest(unittest.TestCase):
    def test_measured_task_becomes_a_succeeded_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            _write_log(ws.logs / "seat-a-8096.log", _header() + _task(0, 1, 100.0, prompt_n=15835, predicted_n=96))
            report = ws.run()
            totals = report["totals"]
            self.assertEqual((totals["eligible_logs"], totals["skipped_logs"], totals["seats"]), (1, 0, 1))
            self.assertEqual((totals["launched"], totals["timed"], totals["unknown"], totals["pending"]), (1, 1, 0, 0))
            self.assertEqual((totals["tokens_in"], totals["tokens_out"], totals["new_receipts"]), (15835, 96, 1))
            rows = ws.rows()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            validate_receipt(row)
            self.assertEqual(row["schema"], SCHEMA)
            self.assertEqual(row["outcome"], "succeeded")
            self.assertEqual(row["usage"], {"tokens_in": 15835, "tokens_out": 96})
            self.assertEqual(row["timing"], {"prompt_ms": 120.5, "predicted_ms": 40.25})
            self.assertEqual(row["provider"]["model_name"], "secret-model")
            self.assertEqual(row["log_basename"], "seat-a-8096")
            expected_epoch = datetime.fromtimestamp(MTIME, tz=timezone.utc) - timedelta(seconds=103.0)
            self.assertEqual(row["started_at"], serverlog.rfc3339(expected_epoch + timedelta(seconds=100.0)))
            self.assertEqual(row["finished_at"], serverlog.rfc3339(expected_epoch + timedelta(seconds=102.0)))
            self.assertEqual(row["timestamp_derivation"]["epoch_start"], serverlog.rfc3339(expected_epoch))
            self.assertEqual(row["harvested_at"], serverlog.rfc3339(NOW))

    def test_untimed_task_is_unknown_only_when_settled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            log = ws.logs / "seat-8096.log"
            fresh_mtime = (NOW - timedelta(seconds=60)).timestamp()
            _write_log(log, _header() + _task(0, 5, 100.0, timed=False, release=False), mtime=fresh_mtime)
            report = ws.run()
            self.assertEqual((report["totals"]["unknown"], report["totals"]["pending"]), (0, 1))
            self.assertFalse(ws.ledger.exists())

            # A later launch on the same slot settles it.
            _write_log(log, _header() + _task(0, 5, 100.0, timed=False, release=False) + _task(0, 6, 200.0),
                       mtime=fresh_mtime + 1)
            report = ws.run()
            self.assertEqual((report["totals"]["unknown"], report["totals"]["pending"], report["totals"]["timed"]), (1, 0, 1))
            unknown = [r for r in ws.rows() if r["outcome"] == "unknown"]
            self.assertEqual(len(unknown), 1)
            self.assertEqual(unknown[0]["task"], 5)
            self.assertIsNone(unknown[0]["usage"])
            self.assertIsNone(unknown[0]["timing"])
            self.assertTrue(unknown[0]["usage_unknown_reason"])
            validate_receipt(unknown[0])

        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            log = ws.logs / "seat-8096.log"
            # A release line with no timing block settles it at once.
            _write_log(log, _header() + _task(1, 76, 100.0, timed=False, release=True),
                       mtime=(NOW - timedelta(seconds=60)).timestamp())
            report = ws.run()
            self.assertEqual((report["totals"]["unknown"], report["totals"]["pending"]), (1, 0))

        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            log = ws.logs / "seat-8096.log"
            # Quiescence settles it too.
            _write_log(log, _header() + _task(0, 5, 100.0, timed=False, release=False),
                       mtime=(NOW - timedelta(seconds=QUIESCENT_S + 60)).timestamp())
            report = ws.run()
            self.assertEqual((report["totals"]["unknown"], report["totals"]["pending"]), (1, 0))

    def test_probe_shaped_task_is_counted_and_tallied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            _write_log(ws.logs / "seat-8095.log", _header(port=8095) + _task(0, 1, 100.0, prompt_n=1, predicted_n=1))
            report = ws.run()
            totals = report["totals"]
            self.assertEqual((totals["timed"], totals["probe_shaped"], totals["new_receipts"]), (1, 1, 1))
            self.assertEqual(ws.rows()[0]["usage"], {"tokens_in": 1, "tokens_out": 1})

    def test_ineligible_logs_are_skipped_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            _write_log(ws.logs / "managed-seat.log", _header(port=18400, api_key=True) + _task(0, 1, 100.0))
            _write_log(ws.logs / "foreign-port-8082.log", _header(port=8082) + _task(0, 1, 100.0))
            _write_log(ws.logs / "still-loading.log", _header(port=None))
            _write_log(ws.logs / "twin-8096.log.console", _header() + _task(0, 1, 100.0))
            report = ws.run()
            by_name = {row["basename"]: row for row in report["logs"]}
            self.assertEqual(set(by_name), {"managed-seat", "foreign-port-8082", "still-loading"})
            self.assertEqual(by_name["managed-seat"]["skip_reason"], SKIP_DOOR_REACHABLE)
            self.assertEqual(by_name["foreign-port-8082"]["skip_reason"], SKIP_PORT)
            self.assertEqual(by_name["still-loading"]["skip_reason"], SKIP_NO_LISTEN)
            self.assertEqual(report["totals"]["skipped_logs"], 3)
            self.assertEqual(report["totals"]["new_receipts"], 0)
            self.assertFalse(ws.ledger.exists())
            cursor = json.loads(ws.cursor.read_text(encoding="utf-8"))
            by_stem = {Path(k).stem: v for k, v in cursor["logs"].items()}
            self.assertEqual(set(by_stem), {"managed-seat", "foreign-port-8082", "still-loading"})
            self.assertFalse(by_stem["managed-seat"]["eligible"])

    def test_short_log_is_deferred_until_the_server_listens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            # Still loading: a few lines and no listening line yet.
            _write_log(ws.logs / "seat-8096.log", _header(stamped=10, port=None))
            report = ws.run()
            self.assertEqual(report["logs"][0]["skip_reason"], SKIP_SHORT)
            self.assertFalse(ws.ledger.exists())
            cursor = json.loads(ws.cursor.read_text(encoding="utf-8"))
            self.assertEqual(cursor["logs"], {})
            # A terse seat whose whole load report is short but complete is eligible.
            _write_log(ws.logs / "seat-8096.log", _header(stamped=10) + _task(0, 1, 100.0), mtime=MTIME + 5)
            report = ws.run(now=NOW + timedelta(minutes=15))
            self.assertTrue(report["logs"][0]["eligible"])
            self.assertEqual(report["totals"]["new_receipts"], 1)

    def test_reharvest_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            _write_log(ws.logs / "seat-8096.log", _header() + _task(0, 1, 100.0) + _task(0, 2, 200.0))
            first = ws.run()
            self.assertEqual(first["totals"]["new_receipts"], 2)
            ledger_sha, cursor_sha = _sha(ws.ledger), _sha(ws.cursor)
            second = ws.run(now=NOW + timedelta(minutes=15))
            self.assertEqual(second["totals"]["new_receipts"], 0)
            self.assertEqual((second["totals"]["launched"], second["totals"]["timed"]), (2, 2))
            self.assertFalse(second["logs"][0]["changed"])
            self.assertEqual(_sha(ws.ledger), ledger_sha)
            self.assertEqual(_sha(ws.cursor), cursor_sha)
            # A forced full rescan also writes nothing: identity is the guard.
            os.utime(ws.logs / "seat-8096.log", (MTIME + 1, MTIME + 1))
            third = ws.run(now=NOW + timedelta(minutes=30))
            self.assertEqual(third["totals"]["new_receipts"], 0)
            self.assertEqual(_sha(ws.ledger), ledger_sha)

    def test_growing_log_adds_only_the_new_task_under_the_same_seat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            log = ws.logs / "seat-8096.log"
            header = _header()
            _write_log(log, header + _task(0, 1, 100.0))
            first = ws.run()
            seat_id = first["logs"][0]["seat_id"]
            _write_log(log, header + _task(0, 1, 100.0) + _task(0, 2, 200.0), mtime=MTIME + 100)
            second = ws.run(now=NOW + timedelta(minutes=15))
            self.assertEqual(second["totals"]["new_receipts"], 1)
            self.assertEqual(second["logs"][0]["seat_id"], seat_id)
            rows = ws.rows()
            self.assertEqual(len(rows), 2)
            self.assertEqual({r["seat_id"] for r in rows}, {seat_id})
            # The epoch was pinned on the first tick: both rows share one derivation.
            self.assertEqual(len({r["timestamp_derivation"]["epoch_start"] for r in rows}), 1)

    def test_rewritten_log_is_a_new_epoch_and_old_rows_survive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            log = ws.logs / "seat-8096.log"
            _write_log(log, _header() + _task(0, 1, 100.0) + _task(0, 2, 200.0))
            first = ws.run()
            old_seat = first["logs"][0]["seat_id"]
            # A relaunch under the same label: fresh header stamps, fewer bytes.
            _write_log(log, _header(filler_offset=500) + _task(0, 1, 50.0), mtime=MTIME + 500)
            second = ws.run(now=NOW + timedelta(minutes=15))
            new_seat = second["logs"][0]["seat_id"]
            self.assertNotEqual(new_seat, old_seat)
            self.assertEqual(second["totals"]["new_receipts"], 1)
            rows = ws.rows()
            self.assertEqual(len(rows), 3)
            self.assertEqual(len({r["attempt_id"] for r in rows}), 3)
            self.assertEqual(sum(1 for r in rows if r["seat_id"] == old_seat), 2)
            cursor = json.loads(ws.cursor.read_text(encoding="utf-8"))
            by_stem = {Path(k).stem: v for k, v in cursor["logs"].items()}
            self.assertEqual(by_stem["seat-8096"]["seat_id"], new_seat)

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            _write_log(ws.logs / "seat-8096.log", _header() + _task(0, 1, 100.0))
            report = ws.run(dry_run=True)
            self.assertTrue(report["dry_run"])
            self.assertEqual(report["totals"]["new_receipts"], 1)
            self.assertFalse(ws.ledger.parent.exists())
            self.assertFalse(ws.cursor.exists())

    def test_receipts_validate_and_never_carry_a_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            _write_log(ws.logs / "seat-8096.log", _header() + _task(0, 1, 100.0)
                       + _task(1, 2, 150.0, timed=False, release=True))
            ws.run()
            text = ws.ledger.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"[A-Za-z]:\\")
            self.assertNotIn(str(ws.logs), text)
            for row in ws.rows():
                validate_receipt(row)
            good = ws.rows()[0]
            bad = json.loads(json.dumps(good))
            bad["provider"]["model_name"] = r"E:\models\secret.gguf"
            with self.assertRaisesRegex(SeatReceiptError, "filesystem path"):
                validate_receipt(bad)
            bad = json.loads(json.dumps(good))
            bad["usage"]["tokens_in"] = -1
            with self.assertRaisesRegex(SeatReceiptError, "nonnegative"):
                validate_receipt(bad)
            bad = json.loads(json.dumps(good))
            bad["extra"] = 1
            with self.assertRaisesRegex(SeatReceiptError, "bad receipt keys"):
                validate_receipt(bad)
            bad = json.loads(json.dumps(good))
            bad["source"] = {"transport": "external", "adapter": "deepagents-direct",
                             "execution_mode": "external", "accounting_owner": "direct"}
            with self.assertRaisesRegex(SeatReceiptError, "seat-log harvest"):
                validate_receipt(bad)

    def test_append_fails_loudly_while_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            _write_log(ws.logs / "seat-8096.log", _header() + _task(0, 1, 100.0))
            ws.run()
            row = ws.rows()[0]
            row["attempt_id"] = "att_" + "0" * 32
            ledger = SeatReceiptsLedger(ws.ledger)
            handle = open(ledger.lock_path, "a+b")
            try:
                _lock_file(handle)
                with self.assertRaisesRegex(SeatReceiptError, "append lock"):
                    ledger.append([row], timeout_s=0.3)
            finally:
                _unlock_file(handle)
                handle.close()
            self.assertEqual(len(ws.rows()), 1)

    def test_campaign_seat_without_header_lines_is_harvested_as_unrecorded(self) -> None:
        # A -lv 3 campaign seat prints no build line and no general.name, only n_ctx_slot.
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            _write_log(ws.logs / "baseline-p18100.stderr.log",
                       _header(port=18100, build=False, model=False, n_ctx_seq=False, n_ctx_slot=True)
                       + _task(0, 1, 100.0, prompt_n=101, predicted_n=64))
            report = ws.run()
            self.assertEqual(report["totals"]["eligible_logs"], 1)
            self.assertEqual(report["totals"]["new_receipts"], 1)
            row = ws.rows()[0]
            validate_receipt(row)
            self.assertEqual(row["provider"]["model_name"], "unrecorded")
            self.assertEqual(row["provider"]["build"], "unrecorded")
            self.assertEqual(row["provider"]["n_ctx_seq"], 65536)
            self.assertEqual(row["log_basename"], "baseline-p18100.stderr")

    def test_multiple_roots_recurse_and_keep_same_basenames_apart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            other = Path(tmp) / "campaign"
            (other / "b5-dense-moe").mkdir(parents=True)
            _write_log(ws.logs / "seat.log", _header() + _task(0, 1, 100.0))
            _write_log(other / "seat.log", _header(port=18195, filler_offset=300) + _task(0, 1, 100.0) + _task(0, 2, 200.0))
            _write_log(other / "b5-dense-moe" / "dense.err.log", _header(port=18196, filler_offset=600) + _task(0, 1, 100.0))
            _write_log(other / "twin.log.console", _header() + _task(0, 1, 100.0))
            report = harvest([ws.logs, other], ws.ledger, ws.cursor, now=NOW)
            self.assertEqual(report["totals"]["eligible_logs"], 3)
            self.assertEqual(report["totals"]["seats"], 3)
            self.assertEqual(report["totals"]["new_receipts"], 4)
            self.assertEqual(len({r["seat_id"] for r in ws.rows()}), 3)
            cursor = json.loads(ws.cursor.read_text(encoding="utf-8"))
            self.assertEqual(len(cursor["logs"]), 3)
            # A missing root is an absence, not an error.
            again = harvest([ws.logs, other, Path(tmp) / "absent"], ws.ledger, ws.cursor, now=NOW)
            self.assertEqual(again["totals"]["new_receipts"], 0)

    def test_render_table_names_every_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            _write_log(ws.logs / "seat-8096.log", _header() + _task(0, 1, 100.0))
            _write_log(ws.logs / "managed.log", _header(port=18400, api_key=True))
            table = harvest_mod.render_table(ws.run(dry_run=True))
            self.assertIn("seat-8096", table)
            self.assertIn(SKIP_DOOR_REACHABLE[:20], table)
            self.assertIn("dry run", table)


if __name__ == "__main__":
    unittest.main()
