"""Track C: the labels materializer (tools/workflow/project_labels.py + labels_derive.py).

Properties from the brief:
  1. off mode opens nothing and never imports the derive module (the three precursor proofs);
  2. each label's method on each fixture run (Track-B events, historic manifest-only, rejected without
     bucket, legacy "Review minutes" prose);
  3. elapsed and queue minutes exact (event fetch and ledger fetch);
  4. the transcript proxy splits across two concurrent reviews and a Monitor-tick burst adds no active time;
  5. a Local-Work trailer commit is detected;
  6. a late verdict (history newer than labels.json) is re-swept;
  7. the sidecar write is atomic (a failed replace leaves the old bytes);
  8. rollup coverage counts and calibration rows;
  9. a sweep over copies of real work dirs changes nothing but adds labels.json.
"""

from __future__ import annotations

import filecmp
import io
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase, mock, skipUnless

from tools.workflow import labels_derive as ld
from tools.workflow import project_labels as entry

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "workflow" / "labels"
REAL_RUNS = ROOT / "runs" / "operator"
DERIVE_MODULE = "tools.workflow.labels_derive"
PY = sys.executable

R1 = "work_" + "a1" * 16  # Track-B style, accepted
R2 = "work_" + "b2" * 16  # historic manifest-only, unattended submission, ledger fetch
R3 = "work_" + "c3" * 16  # rejected without bucket, ledger fetch by codex-cli
R4 = "work_" + "d4" * 16  # accepted, legacy prose, rework of R2
SID_A = "aaaaaaaa-1111-4111-8111-111111111111"
SID_B = "bbbbbbbb-2222-4222-8222-222222222222"
COMMIT_DATE = "2026-10-16T12:00:00+00:00"


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_DATE=COMMIT_DATE, GIT_COMMITTER_DATE=COMMIT_DATE)
    proc = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          capture_output=True, text=True, env=env, check=True)
    return proc.stdout.strip()


class TempDirs(TestCase):
    """A fresh copy of the fixture runs (the sweep writes labels.json into them), the fixture ledger and
    transcripts read in place, a temp git repo with one Local-Work trailer commit."""

    def setUp(self) -> None:
        self.tmp = Path(mkdtemp(prefix="labels-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runs = self.tmp / "runs"
        shutil.copytree(FIXTURES / "runs", self.runs)
        self.knowledge = self.tmp / "knowledge"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "commit", "-q", "--allow-empty", "-m", "apply the ct stats flag", "-m", f"Local-Work: {R1}")
        self.config = self.tmp / "config.toml"
        self.config.write_text(f'[repos.fixture]\npath = "{self.repo}"\n\n[repos.missing]\npath = "{self.tmp}/nowhere"\n',
                               encoding="utf-8")

    def ctx_kwargs(self, **overrides) -> dict:
        kwargs = {"ledger_dir": FIXTURES / "ledger", "transcripts_root": FIXTURES / "transcripts",
                  "continuity_config": self.config, "corpus_path": FIXTURES / "corpus" / "runs.jsonl"}
        kwargs.update(overrides)
        return kwargs

    def env(self, mode: str) -> dict:
        env = dict(os.environ)
        env.update({entry.MODE_ENV: mode, entry.RUNS_ROOT_ENV: str(self.runs), entry.KNOWLEDGE_ENV: str(self.knowledge),
                    entry.LEDGER_ENV: str(FIXTURES / "ledger"), entry.TRANSCRIPTS_ENV: str(FIXTURES / "transcripts"),
                    entry.CONTINUITY_ENV: str(self.config), entry.CORPUS_ENV: str(FIXTURES / "corpus" / "runs.jsonl"),
                    "PYTHONPATH": str(ROOT)})
        return env

    def sweep(self, **overrides) -> dict:
        return ld.sweep(self.runs, self.ctx_kwargs(**overrides), now=_ts("2026-10-16T13:00:00Z"))

    def labels(self, run_id: str) -> dict:
        return json.loads((self.runs / run_id / ld.LABELS_FILE).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- 1. off mode

class OffMode(TempDirs):
    def test_off_is_default_and_opens_nothing_in_process(self):
        """builtins.open fails on every path; main() must still answer {"state": "off"}."""
        real_open = open
        module_path = Path(entry.__file__).resolve()

        def guarded_open(file, *args, **kwargs):
            if Path(str(file)).resolve() == module_path:
                return real_open(file, *args, **kwargs)
            raise AssertionError(f"off mode opened {file!r}")

        buffer = io.StringIO()
        with mock.patch.dict(os.environ, {entry.MODE_ENV: "off"}), \
                mock.patch("builtins.open", side_effect=guarded_open), \
                mock.patch("sys.stdout", buffer):
            rc = entry.main(["--sweep", "--rollup", "--runs-root", str(self.runs), "--out", str(self.knowledge)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buffer.getvalue()), {"state": "off"})
        self.assertFalse(self.knowledge.exists())
        self.assertFalse(any((self.runs / r / ld.LABELS_FILE).exists() for r in (R1, R2, R3, R4)))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(entry.MODE_ENV, None)
            self.assertEqual(entry.labels_mode(), "off")
            os.environ[entry.MODE_ENV] = "nonsense"
            self.assertEqual(entry.labels_mode(), "off")
            os.environ[entry.MODE_ENV] = "ON"
            self.assertEqual(entry.labels_mode(), "on")

    def test_off_never_imports_the_derive_module(self):
        """A fresh interpreter: after main() the derive module, corpus_guard and sqlite3 are absent."""
        code = ("import sys, json\n"
                "from tools.workflow import project_labels as m\n"
                "rc = m.main(['--sweep', '--rollup'])\n"
                f"assert {DERIVE_MODULE!r} not in sys.modules, sorted(k for k in sys.modules if 'labels' in k)\n"
                "assert 'tools.workflow.corpus_guard' not in sys.modules\n"
                "assert 'sqlite3' not in sys.modules\n"
                "print(json.dumps({'rc': rc, 'derive_loaded': " + repr(DERIVE_MODULE) + " in sys.modules}))\n")
        proc = subprocess.run([PY, "-c", code], cwd=str(ROOT), env=self.env("off"), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('{"state": "off"}', proc.stdout)
        self.assertIn('"derive_loaded": false', proc.stdout)

    @skipUnless(shutil.which("strace"), "strace not installed")
    def test_off_opens_no_file_outside_the_interpreter_under_strace(self):
        proc = subprocess.run(["strace", "-f", "-e", "trace=openat,connect", "-o", str(self.tmp / "trace.txt"), PY,
                               str(ROOT / "tools" / "workflow" / "project_labels.py"), "--sweep", "--rollup"],
                              cwd=str(ROOT), env=self.env("off"), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('{"state": "off"}', proc.stdout)
        trace = (self.tmp / "trace.txt").read_text(encoding="utf-8")
        opened = [line for line in trace.splitlines() if "openat(" in line and "ENOENT" not in line]
        forbidden = [line for line in opened if any(token in line for token in (
            str(self.runs), str(self.knowledge), str(FIXTURES), "labels_derive", "corpus_guard", "runs/operator",
            "labels.json", "index.sqlite", "events.ndjson", ".claude/projects", ".continuity"))]
        self.assertEqual(forbidden, [], forbidden)
        self.assertNotIn("connect(", trace)


# --------------------------------------------------------------------------- 2-5. derivations per fixture run

class Derivations(TempDirs):
    def setUp(self) -> None:
        super().setUp()
        counts = self.sweep()
        self.assertEqual(counts["written"], 4, counts)
        self.assertEqual(counts["generator_errors"], 0)
        self.assertEqual(counts["errors"], [])

    def _lab(self, run_id: str, name: str) -> dict:
        doc = self.labels(run_id)
        self.assertEqual(doc["schema"], ld.SCHEMA)
        self.assertEqual(doc["run_id"], run_id)
        self.assertEqual(set(doc["labels"]), set(ld.LABEL_NAMES))
        self.assertEqual(set(doc["coverage"]), set(ld.LABEL_NAMES))
        lab = doc["labels"][name]
        self.assertIn(lab["method"], ld.METHODS, name)
        self.assertEqual(doc["coverage"][name], lab["value"] is not None, name)
        return lab

    def test_track_b_run_reads_every_label_from_events(self):
        for name in ("decision", "bucket", "attended", "verdict_task_id", "verdict_at", "task_family", "lane",
                     "selected_lane", "serving_profile_sha256", "route_profile_sha256", "procedure",
                     "lab_configuration", "temperature", "provider", "model", "first_fetch_at", "first_fetch_by"):
            self.assertEqual(self._lab(R1, name)["method"], "event", name)
        self.assertEqual(self._lab(R1, "decision")["value"], "accepted")
        self.assertEqual(self._lab(R1, "bucket")["value"], "form")
        self.assertIs(self._lab(R1, "attended")["value"], True)
        self.assertEqual(self._lab(R1, "validator"), {"value": "claude-frontier", "method": "principal",
                                                      "source": "history.ndjson#verification.recorded@seq7",
                                                      "at": "2026-10-16T10:30:00Z"})
        self.assertEqual(self._lab(R1, "verdict_task_id")["value"], "cc-aaaaaaaa")
        self.assertEqual(self._lab(R1, "verdict_at")["value"], "2026-10-16T10:30:00Z")
        self.assertEqual(self._lab(R1, "temperature")["value"], 0.0)
        self.assertEqual(self._lab(R1, "serving_profile_sha256")["value"], "ab" * 32)
        self.assertEqual(self._lab(R1, "route_profile_sha256")["value"], "cd" * 32)
        self.assertEqual(self._lab(R1, "submitted_by")["value"], "claude-frontier")
        self.assertIs(self._lab(R1, "unattended_submission")["value"], False)
        self.assertIs(self._lab(R1, "reversal")["value"], False)
        criteria = self._lab(R1, "criteria")
        self.assertEqual(criteria["method"], "event")
        self.assertEqual({k: criteria["value"][k] for k in ("passed", "failed", "unknown")}, {"passed": 2, "failed": 0, "unknown": 0})
        row = criteria["value"]["rows"][0]
        self.assertEqual(row["criterion_sha12"], ld.sha256_text("prints one JSON object")[:12])
        self.assertEqual(row["evidence_sha12"], ld.sha256_text(ld.canonical("python ct.py stats --json | jq . ok"))[:12])
        self.assertEqual(row["status"], "passed")
        # L5 from delivery.json and the manifest
        self.assertEqual(self._lab(R1, "repairs"), {"value": {"ambiguous_quote": 1}, "method": "event",
                                                    "source": "delivery.json:repairs", "at": self._lab(R1, "repairs")["at"]})
        self.assertEqual(self._lab(R1, "claims")["value"], 3)
        self.assertEqual(self._lab(R1, "resolved")["value"], 2)
        self.assertEqual(self._lab(R1, "unsupported")["value"], 1)
        self.assertEqual(self._lab(R1, "aids_used")["value"], ["source_map", "quote_renderer"])
        self.assertEqual(self._lab(R1, "deterministic")["value"], "pass")
        self.assertIs(self._lab(R1, "suspected_incomplete")["value"], False)
        self.assertEqual(self._lab(R1, "structured_output_repairs")["value"], ["control_char_in_string"])
        self.assertEqual(self._lab(R1, "structured_output_repairs")["method"], "manifest_post_outcome")
        self.assertEqual((self._lab(R1, "tokens_in")["value"], self._lab(R1, "tokens_out")["value"],
                          self._lab(R1, "duration_ms")["value"]), (3000, 900, 15000))
        self.assertEqual(self._lab(R1, "tokens_in")["method"], "manifest_post_outcome")

    def test_historic_run_falls_back_to_the_manifest_and_the_ledger(self):
        decision = self._lab(R2, "decision")
        self.assertEqual((decision["value"], decision["method"]), ("accepted", "manifest_post_outcome"))
        self.assertEqual(decision["source"], "work-manifest.json:verdict")
        self.assertIsNone(self._lab(R2, "verdict_at")["value"])
        self.assertEqual(self._lab(R2, "validator")["method"], "manifest_post_outcome")
        self.assertIsNone(self._lab(R2, "bucket")["value"])
        self.assertIsNone(self._lab(R2, "attended")["value"])
        for name in ("task_family", "lane", "selected_lane", "serving_profile_sha256", "route_profile_sha256",
                     "procedure", "lab_configuration", "temperature"):
            self.assertEqual(self._lab(R2, name)["method"], "manifest_post_outcome", name)
        self.assertEqual(self._lab(R2, "task_family")["value"], "quote_retrieval")
        self.assertEqual(self._lab(R2, "lane")["value"], "auto")
        self.assertIsNone(self._lab(R2, "temperature")["value"])
        # provider and model exist in the old step.dispatched payload: those stay pre-outcome
        self.assertEqual(self._lab(R2, "provider"), {"value": "omen-dense-27b-b", "method": "event",
                                                     "source": "history.ndjson#step.dispatched@seq2", "at": "2026-10-01T07:00:10Z"})
        self.assertEqual(self._lab(R2, "model")["method"], "event")
        self.assertEqual(self._lab(R2, "submitted_by")["value"], "bankedfire-drain")
        self.assertIs(self._lab(R2, "unattended_submission")["value"], True)
        fetch = self._lab(R2, "first_fetch_at")
        self.assertEqual((fetch["value"], fetch["method"], fetch["source"]), ("2026-10-01T07:20:00Z", "derived_ledger", "ledger:events/led-0002"))
        self.assertEqual(self._lab(R2, "first_fetch_by")["value"], "claude-frontier")
        self.assertEqual(self._lab(R2, "queue_delay_minutes")["value"], 15.0)
        self.assertIsNone(self._lab(R2, "review_elapsed_minutes")["value"])
        self.assertEqual(self._lab(R2, "criteria")["method"], "manifest_post_outcome")
        self.assertEqual(self._lab(R2, "criteria")["value"]["passed"], 1)
        self.assertIsNone(self._lab(R2, "reversal")["value"])
        for name in ("repairs", "claims", "aids_used", "deterministic"):
            self.assertIsNone(self._lab(R2, name)["value"], name)
        self.assertEqual(self._lab(R2, "structured_output_repairs")["value"], [])

    def test_rejected_without_bucket_keeps_the_null_and_names_the_ledger_fetch(self):
        self.assertEqual(self._lab(R3, "decision")["value"], "rejected")
        bucket = self._lab(R3, "bucket")
        self.assertIsNone(bucket["value"])
        self.assertIn("historic rejection without bucket", bucket["note"])
        self.assertIsNone(self._lab(R3, "attended")["value"])
        self.assertEqual(self._lab(R3, "validator")["value"], "codex-cli")
        task = self._lab(R3, "verdict_task_id")
        self.assertEqual((task["value"], task["method"], task["source"]), ("cc-bbbbbbbb", "derived_transcript", f"transcript:{SID_B}"))
        fetch = self._lab(R3, "first_fetch_at")
        self.assertEqual((fetch["value"], fetch["method"]), ("2026-10-16T08:13:00Z", "derived_ledger"),
                         "the earliest ledger fetch wins, not the later claude-frontier one")
        self.assertEqual(self._lab(R3, "first_fetch_by")["value"], "codex-cli")
        self.assertEqual(self._lab(R3, "queue_delay_minutes")["value"], 10.0)
        elapsed = self._lab(R3, "review_elapsed_minutes")
        self.assertEqual((elapsed["value"], elapsed["method"], elapsed["note"]), (20.0, "derived_events", "fetch:derived_ledger"))
        self.assertIsNone(self._lab(R3, "submit_to_accept_minutes")["value"])
        self.assertEqual(self._lab(R3, "criteria")["value"]["failed"], 1)
        self.assertEqual(self._lab(R3, "criteria")["value"]["rows"][0]["status"], "failed")
        self.assertIs(self._lab(R3, "applied")["value"], False)
        self.assertEqual(self._lab(R3, "applied")["method"], "git_trailer")

    def test_legacy_prose_self_report_and_rework(self):
        report = self._lab(R4, "review_minutes_self_report")
        self.assertEqual((report["value"], report["method"], report["note"]), (3.0, "self_report", "legacy_prose"))
        self.assertEqual(report["source"], "work-manifest.json:verdict.summary")
        kv = self._lab(R1, "review_minutes_self_report")
        self.assertEqual((kv["value"], kv["source"]), (6.0, "work-manifest.json:verdict.evidence"))
        self.assertNotEqual(kv.get("note"), "legacy_prose")
        self.assertIsNone(self._lab(R4, "first_fetch_at")["value"])
        self.assertIsNone(self._lab(R4, "queue_delay_minutes")["value"])
        self.assertIsNone(self._lab(R4, "review_elapsed_minutes")["value"])
        rework = self._lab(R4, "rework")
        self.assertIs(rework["value"], True)
        self.assertEqual(rework["method"], "derived_events")
        self.assertIn("g_streams_brief", rework["source"])
        self.assertIn("corpus_group", rework["source"])
        self.assertIn(R2, rework["note"])
        self.assertIs(self._lab(R2, "rework")["value"], False)
        self.assertIs(self._lab(R1, "rework")["value"], False)
        self.assertIn("intent_sha256", self._lab(R1, "rework")["source"], "R1 is not in the corpus: intent grouping")
        self.assertEqual(self._lab(R4, "submit_to_accept_minutes")["value"], 50.0)
        self.assertEqual(self._lab(R1, "submit_to_accept_minutes")["value"], 30.0)

    def test_elapsed_and_queue_minutes_exact_from_events(self):
        self.assertEqual(self._lab(R1, "queue_delay_minutes"), {
            "value": 8.0, "method": "derived_events", "at": "2026-10-16T10:10:00Z", "note": "fetch:event",
            "source": "history.ndjson#artifact.fetched@seq6 - history.ndjson#artifact.produced@seq4"})
        self.assertEqual(self._lab(R1, "review_elapsed_minutes"), {
            "value": 20.0, "method": "derived_events", "at": "2026-10-16T10:30:00Z", "note": "fetch:event",
            "source": "verdict_at - history.ndjson#artifact.fetched@seq6"})

    def test_transcript_proxy_splits_across_two_concurrent_reviews(self):
        r1 = self._lab(R1, "review_active_minutes_transcript")
        r4 = self._lab(R4, "review_active_minutes_transcript")
        self.assertEqual(r1["method"], "derived_transcript")
        self.assertEqual(r1["source"], f"transcript:{SID_A}")
        self.assertEqual(self._lab(R1, "concurrent_reviews")["value"], 2)
        self.assertEqual(self._lab(R4, "concurrent_reviews")["value"], 2)
        self.assertAlmostEqual(r1["value"], 370 / 2 / 60, places=2)
        self.assertAlmostEqual(r4["value"], 430 / 2 / 60, places=2)
        self.assertEqual(self._lab(R3, "concurrent_reviews")["value"], 1)
        self.assertEqual(self._lab(R3, "review_active_minutes_transcript")["value"], 1.0)
        self.assertIsNone(self._lab(R2, "review_active_minutes_transcript")["value"])

    def test_tick_burst_adds_no_active_time(self):
        scan = ld.scan_transcripts(FIXTURES / "transcripts")
        self.assertEqual(scan["files"], 3)
        self.assertEqual(scan["parsed_files"], 2, "the decoy session only lists the tool in an attachment")
        proxies = ld.transcript_proxies(scan)
        self.assertEqual(set(proxies), {R1, R3, R4})
        self.assertEqual(proxies[R1]["active_seconds"], 370.0)
        self.assertEqual(proxies[R1]["tick_gaps_excluded"], 4)
        self.assertEqual(proxies[R4]["active_seconds"], 430.0, "the single 60 s gap at 10:35->10:36 counts")
        self.assertEqual(proxies[R4]["tick_gaps_excluded"], 4)
        self.assertEqual(proxies[R1]["span_start"], "2026-10-16T10:04:30Z")
        self.assertEqual(proxies[R1]["span_end"], "2026-10-16T10:30:00Z")
        # cadence rule in isolation
        self.assertEqual(ld.tick_gap_mask([60, 60, 60]), [True, True, True])
        self.assertEqual(ld.tick_gap_mask([60, 60]), [False, False])
        self.assertEqual(ld.tick_gap_mask([30, 61, 59, 62, 30]), [False, True, True, True, False])
        self.assertEqual(ld.tick_gap_mask([60, 70, 60, 60, 60]), [False, False, True, True, True])

    def test_transcript_scanner_keeps_only_timestamps_names_and_work_ids(self):
        scan = ld.scan_transcripts(FIXTURES / "transcripts")
        for sess in scan["sessions"].values():
            self.assertEqual(set(sess), {"records", "tool_uses"})
            self.assertTrue(all(isinstance(t, float) for t in sess["records"]))
            for ts, name, work_id in sess["tool_uses"]:
                self.assertIsInstance(ts, float)
                self.assertTrue(name.startswith("mcp__hearth__"))
                self.assertTrue(work_id.startswith("work_"))
        self.assertNotIn("text", json.dumps(scan))

    def test_trailer_commit_is_detected(self):
        applied = self._lab(R1, "applied")
        self.assertEqual((applied["value"], applied["method"], applied["source"]), (True, "git_trailer", f"git:{self.repo}"))
        self.assertEqual(self._lab(R1, "applied_at")["value"], "2026-10-16T12:00:00Z")
        self.assertEqual(self._lab(R1, "applied_commit")["value"], _git(self.repo, "rev-parse", "HEAD"))
        scan = ld.scan_git_trailers(self.config)
        self.assertEqual([r["name"] for r in scan["repos"]], ["fixture", "missing"])
        self.assertEqual(scan["repos"][1]["error"], "missing")
        self.assertEqual(set(scan["by_work_id"]), {R1})

    def test_ledger_scan_matches_only_artifact_fetches(self):
        scan = ld.scan_ledger(FIXTURES / "ledger")
        self.assertIsNone(scan["diagnostic"])
        self.assertEqual(scan["rows"], 5, "five get_local_work_artifact rows, other tools ignored")
        self.assertEqual(scan["matched"], 4, "the artifact_id-only row carries no work_id")
        self.assertEqual(set(scan["by_work_id"]), {R2, R3, "work_" + "e5" * 16})
        self.assertEqual(scan["by_work_id"][R3]["by"], "codex-cli")
        absent = ld.scan_ledger(self.tmp / "no-ledger")
        self.assertTrue(absent["diagnostic"].startswith("ledger absent"))


# --------------------------------------------------------------------------- 6-7. re-sweep and atomicity

class SweepBehaviour(TempDirs):
    def test_late_verdict_resweep_updates_labels(self):
        first = self.sweep()
        self.assertEqual(first["written"], 4)
        second = self.sweep()
        self.assertEqual((second["written"], second["skipped_fresh"]), (0, 4))
        before = self.labels(R2)
        self.assertIsNone(before["labels"]["verdict_at"]["value"])
        history = self.runs / R2 / "history.ndjson"
        with history.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"contract_version": "operator-history-event.v1", "event_id": "late", "event_type": "outcome.final",
                                 "payload": {"status": "accepted", "validator": "claude-frontier", "bucket": "form",
                                             "attended": True, "task_id": "cc-late0000"},
                                 "refs": {}, "run_id": R2, "sequence": 9, "timestamp": "2026-10-16T12:30:00Z"}) + "\n")
        late_ns = time.time_ns() + 2_000_000_000
        os.utime(history, ns=(late_ns, late_ns))
        third = self.sweep()
        self.assertEqual((third["written"], third["reasons"]), (1, {"inputs_newer": 1}))
        after = self.labels(R2)
        self.assertEqual(after["labels"]["decision"]["method"], "event")
        self.assertEqual(after["labels"]["verdict_at"]["value"], "2026-10-16T12:30:00Z")
        self.assertEqual(after["labels"]["bucket"]["value"], "form")
        self.assertEqual(after["labels"]["verdict_task_id"]["value"], "cc-late0000")
        self.assertEqual(after["labels"]["review_elapsed_minutes"]["value"],
                         round((_ts("2026-10-16T12:30:00Z") - _ts("2026-10-01T07:20:00Z")).total_seconds() / 60, 2))
        self.assertEqual(after["labels"]["submit_to_accept_minutes"]["value"], 15 * 24 * 60 + 330.0)
        # an older generator is re-swept too
        doc = self.labels(R3)
        doc["generator_version"] = "labels_derive.v0"
        (self.runs / R3 / ld.LABELS_FILE).write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual(ld.needs_labels(self.runs / R3), (True, "generator_version"))
        self.assertEqual(ld.needs_labels(self.runs / R1), (False, "fresh"))
        self.assertEqual(ld.needs_labels(self.runs / R1, force=True), (True, "forced"))

    def test_sidecar_write_is_atomic(self):
        self.sweep()
        target = self.runs / R1 / ld.LABELS_FILE
        before = target.read_bytes()
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            counts = ld.sweep(self.runs, self.ctx_kwargs(), force=True)
        self.assertEqual(counts["written"], 0)
        self.assertEqual(len(counts["errors"]), 4)
        self.assertIn("disk full", counts["errors"][0]["error"])
        self.assertEqual(target.read_bytes(), before)
        for leftover in self.runs.glob("*/labels.json.tmp"):
            leftover.unlink()
        counts = ld.sweep(self.runs, self.ctx_kwargs(), force=True)
        self.assertEqual(counts["written"], 4)
        self.assertEqual(sorted(p.name for p in (self.runs / R1).iterdir()),
                         ["delivery.json", "history.ndjson", "labels.json", "refs", "work-manifest.json"])

    def test_a_derivation_fault_becomes_generator_error_not_an_exception(self):
        broken = self.runs / ("work_" + "f6" * 16)
        broken.mkdir()
        (broken / "work-manifest.json").write_text(json.dumps({"work_id": "work_" + "f6" * 16, "status": "accepted",
                                                                "route": "not-a-dict", "verdict": {"criteria": 5}}), encoding="utf-8")
        (broken / "history.ndjson").write_text("{not json\n", encoding="utf-8")
        with mock.patch.object(ld, "derive_l5", side_effect=KeyError("boom")):
            counts = self.sweep()
        self.assertEqual(counts["written"], 5)
        self.assertEqual(counts["generator_errors"], 5)
        doc = json.loads((broken / ld.LABELS_FILE).read_text(encoding="utf-8"))
        self.assertIn("KeyError", doc["generator_error"])
        self.assertIn("boom", doc["diagnostic"])
        self.assertEqual(doc["labels"], {})
        self.assertFalse(any(doc["coverage"].values()))
        # a manifest-less, history-less dir is skipped, an unreadable history is tolerated
        (self.runs / ("work_" + "07" * 16)).mkdir()
        counts = ld.sweep(self.runs, self.ctx_kwargs(), force=True)
        self.assertEqual(counts["skipped_no_inputs"], 1)
        self.assertEqual(counts["generator_errors"], 0)
        doc = json.loads((broken / ld.LABELS_FILE).read_text(encoding="utf-8"))
        self.assertNotIn("generator_error", doc)
        self.assertEqual(doc["labels"]["decision"]["value"], "accepted")

    def test_scans_degrade_to_diagnostics(self):
        counts = ld.sweep(self.runs, self.ctx_kwargs(ledger_dir=self.tmp / "nope", transcripts_root=None,
                                                     continuity_config=self.tmp / "nope.toml", corpus_path=None))
        self.assertEqual(counts["written"], 4)
        self.assertEqual(counts["generator_errors"], 0)
        doc = self.labels(R3)
        self.assertIsNone(doc["labels"]["first_fetch_at"]["value"])
        self.assertIn("ledger absent", doc["labels"]["first_fetch_at"]["note"])
        self.assertIsNone(doc["labels"]["review_active_minutes_transcript"]["value"])
        self.assertEqual(doc["labels"]["review_active_minutes_transcript"]["note"], "transcript proxy disabled")
        self.assertIsNone(doc["labels"]["applied"]["value"])
        self.assertIn("continuity config absent", doc["labels"]["applied"]["note"])
        self.assertIn("intent_sha256", doc["labels"]["rework"]["source"])
        self.assertIs(self.labels(R4)["labels"]["rework"]["value"], False, "without the corpus R2 and R4 differ in intent")


# --------------------------------------------------------------------------- 8. rollup

class Rollup(TempDirs):
    def test_rollup_coverage_counts_and_calibration(self):
        self.sweep()
        doc = ld.rollup(self.runs, self.knowledge, now=_ts("2026-10-16T13:00:00Z"))
        self.assertEqual(doc["schema"], ld.ROLLUP_SCHEMA)
        self.assertEqual(doc["runs"], 4)
        self.assertEqual(doc["evidence_watermark"], "2026-10-16T10:40:00Z")
        cov = doc["coverage"]
        self.assertEqual(set(cov), set(ld.LABEL_NAMES))
        self.assertEqual(cov["decision"], {"n": 4, "share": 1.0, "by_method": {"event": 3, "manifest_post_outcome": 1}})
        self.assertEqual(cov["bucket"], {"n": 1, "share": 0.25, "by_method": {"event": 1}})
        self.assertEqual(cov["attended"]["n"], 1)
        self.assertEqual(cov["verdict_task_id"]["by_method"], {"derived_transcript": 2, "event": 1})
        self.assertEqual(cov["first_fetch_at"]["by_method"], {"derived_ledger": 2, "event": 1})
        self.assertEqual(cov["review_elapsed_minutes"]["n"], 2)
        self.assertEqual(cov["review_active_minutes_transcript"]["n"], 3)
        self.assertEqual(cov["review_minutes_self_report"]["n"], 2)
        self.assertEqual(cov["task_family"]["by_method"], {"event": 1, "manifest_post_outcome": 3})
        self.assertEqual(cov["applied"], {"n": 4, "share": 1.0, "by_method": {"git_trailer": 4}})
        self.assertEqual(cov["applied_commit"]["n"], 1)
        self.assertEqual(cov["repairs"]["n"], 1)
        self.assertEqual(doc["status_classes"], {"accepted": 3, "rejected": 1})
        self.assertEqual(doc["decisions"], {"accepted": 3, "rejected": 1})
        self.assertEqual(doc["rejection_buckets"], {"none": 1})
        rows = doc["calibration"]["self_report_vs_elapsed"]
        self.assertEqual(len(rows), 1)
        self.assertEqual({k: rows[0][k] for k in ("run_id", "self_report_minutes", "elapsed_minutes", "active_transcript_minutes",
                                                  "concurrent_reviews", "ratio_self_over_elapsed", "fetch_method")},
                         {"run_id": R1, "self_report_minutes": 6.0, "elapsed_minutes": 20.0, "active_transcript_minutes": 3.08,
                          "concurrent_reviews": 2, "ratio_self_over_elapsed": 0.3, "fetch_method": "event"})
        self.assertEqual(doc["calibration"]["summary"]["n"], 1)
        on_disk = json.loads((self.knowledge / ld.ROLLUP_FILE).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["coverage"], doc["coverage"])
        self.assertEqual(sorted(p.name for p in self.knowledge.iterdir()), ["labels.json"], "no temp file left")
        # the guard refuses a regression (fewer runs) without an authored override
        shutil.rmtree(self.runs / R4)
        from tools.workflow.corpus_guard import CorpusRegressionError
        with self.assertRaises(CorpusRegressionError):
            ld.rollup(self.runs, self.knowledge, now=_ts("2026-10-16T14:00:00Z"))

    def test_entry_runs_sweep_and_rollup_on(self):
        proc = subprocess.run([PY, "-m", "tools.workflow.project_labels", "--sweep", "--rollup"], cwd=str(ROOT),
                              env=self.env("on"), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["state"], "on")
        self.assertEqual(summary["sweep"]["written"], 4)
        self.assertEqual(summary["sweep"]["budget_exceeded"], 0)
        self.assertLessEqual(summary["sweep"]["derive_ms_max"], ld.PER_RUN_BUDGET_MS)
        self.assertEqual(summary["rollup"]["runs"], 4)
        self.assertEqual(summary["rollup"]["coverage_n"]["decision"], 4)
        self.assertEqual(summary["rollup"]["calibration_rows"], 1)
        self.assertTrue((self.knowledge / ld.ROLLUP_FILE).is_file())


# --------------------------------------------------------------------------- 9. real work dirs

class SweepLeavesRealDirsUntouched(TempDirs):
    @skipUnless(REAL_RUNS.is_dir(), "real runs/operator absent")
    def test_sweep_over_real_metadata_copies_only_adds_labels_json(self):
        real = sorted(p for p in REAL_RUNS.iterdir() if p.is_dir() and p.name.startswith("work_")
                      and (p / "work-manifest.json").is_file() and (p / "history.ndjson").is_file())[:3]
        if len(real) < 3:
            self.skipTest("fewer than three real work dirs")
        runs = self.tmp / "real-runs"
        original = self.tmp / "real-original"
        for src in real:
            for rel in ("work-manifest.json", "history.ndjson", "delivery.json", "refs/envelope.json"):
                if (src / rel).is_file():
                    for dst_root in (runs, original):
                        dst = dst_root / src.name / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src / rel, dst)
        counts = ld.sweep(runs, self.ctx_kwargs(ledger_dir=self.tmp / "no-ledger", transcripts_root=FIXTURES / "transcripts",
                                                continuity_config=self.config, corpus_path=None))
        self.assertEqual(counts["written"], 3, counts)
        self.assertEqual(counts["generator_errors"], 0, [json.loads((runs / p.name / ld.LABELS_FILE).read_text()).get("generator_error")
                                                         for p in real])
        self.assertLessEqual(counts["derive_ms_max"], ld.PER_RUN_BUDGET_MS)
        new_files = []
        for dirpath, _dirs, files in os.walk(runs):
            for name in files:
                rel = Path(dirpath, name).relative_to(runs)
                twin = original / rel
                if not twin.exists():
                    new_files.append(rel.name)
                    continue
                self.assertTrue(filecmp.cmp(twin, runs / rel, shallow=False), f"{rel} changed")
        for dirpath, _dirs, files in os.walk(original):
            for name in files:
                rel = Path(dirpath, name).relative_to(original)
                self.assertTrue((runs / rel).exists(), f"{rel} removed")
        self.assertEqual(new_files, [ld.LABELS_FILE] * 3)
        for src in real:
            doc = json.loads((runs / src.name / ld.LABELS_FILE).read_text(encoding="utf-8"))
            self.assertEqual(doc["schema"], ld.SCHEMA)
            self.assertEqual(doc["run_id"], src.name)
            self.assertEqual(set(doc["labels"]), set(ld.LABEL_NAMES))
            manifest = json.loads((runs / src.name / "work-manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("labels", manifest)
