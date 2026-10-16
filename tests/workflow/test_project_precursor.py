"""Track D1: the precursor materializer (tools/workflow/project_precursor.py + precursor_index.py).

Six properties from the brief, plus BM25 sanity and the 1,000-row build bound:
  1. off mode opens nothing and never imports the index module;
  2. a row whose first_final_at is after T is never a neighbour and never in n_support;
  3. the query's own run is never a neighbour;
  4. the index is written atomically and deterministically (same input -> same sha256);
  5. --advise changes a work dir only by adding precursor/advice.json;
  6. advice written after a verdict is tagged late and --join excludes it.
"""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase, mock, skipUnless

from tools.workflow import precursor_index as pi
from tools.workflow import project_precursor as entry

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_CORPUS = ROOT / "fixtures" / "workflow" / "precursor" / "v1" / "runs.jsonl"
REAL_WORK_DIR = ROOT / "runs" / "operator" / "work_e967d2d2c46e3952c0b9b3c80fe501bf"
INDEX_MODULE = "tools.workflow.precursor_index"
PY = sys.executable


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _event(run_id: str, kind: str, ts: str, payload: dict, seq: int) -> str:
    return json.dumps({"contract_version": "operator-history-event.v1", "envelope_id": "e" * 64,
                       "event_id": f"{run_id}-{seq}", "event_type": kind, "payload": payload,
                       "refs": {}, "run_id": run_id, "sequence": seq, "timestamp": ts})


def _make_work_dir(root: Path, run_id: str, intent: str, *, dispatched_at: str, final: tuple | None = None,
                   backend: str = "omen-dense-27b", family: str = "code_fix", artifact_at: str | None = None) -> Path:
    path = root / run_id
    (path / "refs").mkdir(parents=True)
    (path / "refs" / "envelope.json").write_text(json.dumps({
        "contract_version": "task-envelope.v1", "intent": intent, "acceptance_criteria": ["it works"],
        "submitted_by": "claude-frontier", "submitted_at": dispatched_at}), encoding="utf-8")
    events = [_event(run_id, "task.received", dispatched_at, {"submitted_by": "claude-frontier"}, 1),
              _event(run_id, "step.dispatched", dispatched_at, {"attempt": 1, "job_id": "job_x", "model": "qwen3.8-27b",
                                                               "provider": backend}, 2)]
    seq = 3
    if artifact_at:
        events.append(_event(run_id, "artifact.produced", artifact_at, {"artifact_id": "art_x"}, seq))
        seq += 1
    if final:
        events.append(_event(run_id, "outcome.final", final[0], {"status": final[1], "validator": "claude-frontier"}, seq))
    (path / "history.ndjson").write_text("\n".join(events) + "\n", encoding="utf-8")
    (path / "work-manifest.json").write_text(json.dumps({
        "schema": "work-manifest.v1", "work_id": run_id, "status": final[1] if final else "awaiting_review",
        "route": {"backend": backend, "provider": backend, "task_family": family,
                  "serving_profile_sha256": "5d11" + "0" * 60}, "verdict": None}), encoding="utf-8")
    return path


def _index_rows_from_fixture() -> list[dict]:
    rows, _, _ = pi.load_corpus(FIXTURE_CORPUS)
    return [pi._index_row(r, "corpus", r["group_id"]) for r in rows]


class TempDirs(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(mkdtemp(prefix="precursor-test-"))
        self.runs = self.tmp / "runs"
        self.runs.mkdir()
        self.knowledge = self.tmp / "knowledge"
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def env(self, mode: str) -> dict:
        env = dict(os.environ)
        env.update({entry.MODE_ENV: mode, entry.CORPUS_ENV: str(FIXTURE_CORPUS), entry.RUNS_ROOT_ENV: str(self.runs),
                    entry.KNOWLEDGE_ENV: str(self.knowledge), entry.RESEARCH_ENV: str(self.tmp / "research"),
                    "PYTHONPATH": str(ROOT)})
        return env


class OffMode(TempDirs):
    def test_off_is_default_and_opens_nothing_in_process(self):
        """builtins.open fails on every path; main() must still answer {"state": "off"}."""
        real_open = open
        module_path = Path(entry.__file__).resolve()

        def guarded_open(file, *args, **kwargs):
            if Path(str(file)).resolve() == module_path:
                return real_open(file, *args, **kwargs)
            raise AssertionError(f"off mode opened {file!r}")

        import io
        buffer = io.StringIO()
        with mock.patch.dict(os.environ, {entry.MODE_ENV: "off"}), \
                mock.patch("builtins.open", side_effect=guarded_open), \
                mock.patch("sys.stdout", buffer):
            rc = entry.main(["--build-index", "--advise", "--join", "--runs-root", str(self.runs),
                             "--out", str(self.knowledge), "--corpus", str(FIXTURE_CORPUS)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buffer.getvalue()), {"state": "off"})
        self.assertFalse(self.knowledge.exists())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(entry.MODE_ENV, None)
            self.assertEqual(entry.precursor_mode(), "off")
            os.environ[entry.MODE_ENV] = "nonsense"
            self.assertEqual(entry.precursor_mode(), "off")

    def test_off_never_imports_the_index_module(self):
        """A fresh interpreter: after main() the index module is absent from sys.modules."""
        code = ("import sys, json\n"
                "from tools.workflow import project_precursor as m\n"
                "rc = m.main(['--build-index', '--advise', '--join'])\n"
                f"assert {INDEX_MODULE!r} not in sys.modules, sorted(k for k in sys.modules if 'precursor' in k)\n"
                "assert 'tools.workflow.corpus_guard' not in sys.modules\n"
                "print(json.dumps({'rc': rc, 'index_loaded': " + repr(INDEX_MODULE) + " in sys.modules}))\n")
        proc = subprocess.run([PY, "-c", code], cwd=str(ROOT), env=self.env("off"), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('{"state": "off"}', proc.stdout)
        self.assertIn('"index_loaded": false', proc.stdout)

    @skipUnless(shutil.which("strace"), "strace not installed")
    def test_off_opens_no_file_outside_the_interpreter_under_strace(self):
        """Plan §5.2: verified by strace -e openat. Every successful openat must be the interpreter's
        own files (stdlib, site-packages, the tools package); none under runs/, knowledge/ or the corpus."""
        proc = subprocess.run(["strace", "-f", "-e", "trace=openat,connect", "-o", str(self.tmp / "trace.txt"), PY,
                               str(ROOT / "tools" / "workflow" / "project_precursor.py"), "--build-index", "--advise",
                               "--join"], cwd=str(ROOT), env=self.env("off"), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('{"state": "off"}', proc.stdout)
        trace = (self.tmp / "trace.txt").read_text(encoding="utf-8")
        opened = [line for line in trace.splitlines() if "openat(" in line and "ENOENT" not in line]
        forbidden = [line for line in opened if any(token in line for token in (
            str(self.runs), str(self.knowledge), str(FIXTURE_CORPUS), "precursor_index", "corpus_guard",
            "runs/operator", "advice.json"))]
        self.assertEqual(forbidden, [], forbidden)
        self.assertNotIn("connect(", trace)


class TemporalRules(TestCase):
    def setUp(self) -> None:
        self.rows = _index_rows_from_fixture()
        self.index = {"schema": pi.INDEX_SCHEMA, "corpus_version": "fixture-v1", "built_at": "2026-12-02T00:00:00Z",
                      "rows": self.rows, "config": pi.DEFAULT_CONFIG, "config_id": pi.config_id_of(pi.DEFAULT_CONFIG),
                      "evidence_watermark": "2026-12-01T10:00:00Z"}

    def _query(self, run_id: str, dispatched_at: str, intent: str, final: str | None = None) -> dict:
        return {"run_id": run_id, "path": "/nonexistent", "intent_text": intent, "intent_sha256": "x", "criteria": [],
                "dispatched_at": dispatched_at, "received_at": dispatched_at, "first_final_at": final,
                "first_final_decision": None, "backend": "omen-dense-27b", "task_family": "code_fix",
                "serving_profile_sha": "5d11", "has_advice": False, "verdict_events": [], "artifact_at": None}

    def test_future_final_is_never_a_neighbour_nor_support(self):
        # work_fx11 is the planted leak: identical topic (--json for ct stats), verdict 2026-12-01.
        query = self._query("work_q1", "2026-09-30T12:00:00Z", "ct stats --json flag in ct/ct.py printing one JSON object")
        rows = pi.advise_run(query, self.index, None, mode="shadow", now=_ts("2026-12-02T00:00:00Z"), stale_reason=None)
        by_arm = {r["arm"]: r for r in rows}
        self.assertEqual(by_arm["bm25"]["state"], "ok")
        neighbours = [n["run_id"] for n in by_arm["bm25"]["neighbours"]]
        self.assertNotIn("work_fx11" + "0" * 28, neighbours)
        self.assertIn("work_fx01" + "0" * 28, neighbours)
        pool = pi.eligible_rows(self.rows, "work_q1", _ts("2026-09-30T12:00:00Z"), pi.DEFAULT_CONFIG)
        pool_ids = {r["run_id"] for r in pool}
        self.assertNotIn("work_fx11" + "0" * 28, pool_ids)
        self.assertNotIn("work_fx10" + "0" * 28, pool_ids, "awaiting_review row has no final: not evidence")
        # table n_support counts code_fix rows on omen-dense-27b with a final at or before T
        self.assertEqual(by_arm["table"]["n_support"], len([r for r in pool if r["backend"] == "omen-dense-27b"
                                                              and r["task_family"] == "code_fix"]))
        self.assertLessEqual(by_arm["bm25"]["n_support"], 5)
        # candidate set is the sorted eligible ids
        expected = pi.sha256_text("\n".join(sorted(pool_ids)))
        self.assertEqual(by_arm["bm25"]["candidate_set_sha256"], expected)

    def test_label_at_T_uses_the_latest_verdict_at_or_before_T(self):
        reversed_row = next(r for r in self.rows if r["run_id"].startswith("work_fx09"))
        self.assertEqual(pi.label_at(reversed_row, _ts("2026-09-28T12:00:00Z")), "rejected")
        self.assertEqual(pi.label_at(reversed_row, _ts("2026-09-30T00:00:00Z")), "accepted")
        self.assertIsNone(pi.label_at(reversed_row, _ts("2026-09-28T09:30:00Z")))

    def test_own_run_is_never_a_neighbour(self):
        own = "work_fx01" + "0" * 28
        own_row = next(r for r in self.rows if r["run_id"] == own)
        query = self._query(own, "2026-10-01T00:00:00Z", own_row["intent_text"])
        rows = pi.advise_run(query, self.index, None, mode="shadow", now=_ts("2026-12-02T00:00:00Z"), stale_reason=None)
        bm25 = next(r for r in rows if r["arm"] == "bm25")
        self.assertEqual(bm25["state"], "ok")
        self.assertNotIn(own, [n["run_id"] for n in bm25["neighbours"]])
        pool = pi.eligible_rows(self.rows, own, _ts("2026-10-01T00:00:00Z"), pi.DEFAULT_CONFIG)
        self.assertNotIn(own, {r["run_id"] for r in pool})
        # the near-duplicate of the same group IS still a neighbour in the operational view
        self.assertIn("work_fx05" + "0" * 28, [n["run_id"] for n in bm25["neighbours"]])

    def test_abstain_below_three_and_late_tag(self):
        query = self._query("work_q2", "2026-09-21T00:00:00Z", "anything", final="2026-09-21T05:00:00Z")
        rows = pi.advise_run(query, self.index, None, mode="shadow", now=_ts("2026-12-02T00:00:00Z"), stale_reason=None)
        for r in rows:
            self.assertEqual(r["state"], "abstain")
            self.assertIsNone(r["p_accept"])
            self.assertIn(pi.LATE_DIAGNOSTIC, r["diagnostic"])
            self.assertIsNone(r["displayed"])
            self.assertEqual(r["mode"], "shadow")
            self.assertEqual(r["view"], "operational")

    def test_missing_index_and_exceptions_become_unavailable(self):
        query = self._query("work_q3", "2026-09-30T00:00:00Z", "x")
        rows = pi.advise_run(query, None, "index missing: /nowhere", mode="shadow", now=_ts("2026-12-02T00:00:00Z"),
                             stale_reason=None)
        self.assertEqual({r["state"] for r in rows}, {"unavailable"})
        broken = dict(self.index, rows=["not-a-row", 42])  # eligible_rows raises AttributeError
        rows = pi.advise_run(query, broken, None, mode="shadow", now=_ts("2026-12-02T00:00:00Z"), stale_reason=None)
        self.assertEqual({r["state"] for r in rows}, {"unavailable"})
        self.assertTrue(all(r["diagnostic"] for r in rows))

    def test_stale_marks_rows_but_still_computes_support(self):
        query = self._query("work_q4", "2026-09-30T12:00:00Z", "ct stats --json flag")
        rows = pi.advise_run(query, self.index, None, mode="shadow", now=_ts("2026-12-02T00:00:00Z"),
                             stale_reason="stale:index_age>24h")
        for r in rows:
            self.assertEqual(r["state"], "stale")
            self.assertIsNone(r["p_accept"])
            self.assertGreater(r["n_support"], 0)

    def test_bm25_ranks_the_overlapping_document_first(self):
        engine = pi.BM25([pi.tokenize("ct stats json flag"), pi.tokenize("night approval grant report"),
                          pi.tokenize("json parse of the stats output")])
        scores = engine.scores(pi.tokenize("stats --json"))
        self.assertEqual(max(range(3), key=lambda i: scores[i]), 0)
        self.assertEqual(scores[1], 0.0)


class AtomicBuild(TempDirs):
    def test_build_is_atomic_and_deterministic(self):
        now = _ts("2026-10-16T09:00:00Z")
        first = pi.build_index(FIXTURE_CORPUS, self.runs, self.knowledge, research_dir=None, now=now)
        path = self.knowledge / pi.INDEX_FILE
        self.assertTrue(path.is_file())
        self.assertEqual(first["row_count"], 11)
        self.assertEqual(first["corpus_version"], "fixture-v1")
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["sha256"], first["sha256"])
        self.assertEqual(sorted(path.parent.iterdir()), [path], "no temp file left beside the index")

        # a failed replace leaves the previous file byte-identical and no partial target
        before = path.read_bytes()
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                pi.build_index(FIXTURE_CORPUS, self.runs, self.knowledge, research_dir=None, now=now)
        self.assertEqual(path.read_bytes(), before)
        for leftover in path.parent.glob("*.tmp"):
            leftover.unlink()

        second = pi.build_index(FIXTURE_CORPUS, self.runs, self.knowledge, research_dir=None,
                                now=_ts("2026-10-16T10:00:00Z"))
        self.assertEqual(second["sha256"], first["sha256"])
        self.assertEqual(second["config_id"], first["config_id"])
        self.assertNotEqual(second["built_at"], first["built_at"])

    def test_live_rows_newer_than_the_corpus_join_the_index(self):
        _make_work_dir(self.runs, "work_live1" + "0" * 27, "a live run", dispatched_at="2026-10-10T10:00:00Z",
                       artifact_at="2026-10-10T10:05:00Z", final=("2026-10-10T11:00:00Z", "accepted"))
        doc = pi.build_index(FIXTURE_CORPUS, self.runs, self.knowledge, research_dir=None)
        self.assertEqual(doc["live_rows"], 1)
        self.assertEqual(doc["row_count"], 12)
        live = next(r for r in doc["rows"] if r["source"] == "live")
        self.assertEqual(live["first_final_at"], "2026-10-10T11:00:00Z")
        self.assertEqual(live["verdict_events"][0]["decision"], "accepted")
        self.assertEqual(doc["evidence_watermark"], "2026-12-01T10:00:00Z")

    def test_frozen_config_changes_config_id(self):
        research = self.tmp / "research" / "results" / "2026-10-16"
        research.mkdir(parents=True)
        (research / "config.json").write_text(json.dumps({"k": 7, "ignored_key": 1}), encoding="utf-8")
        doc = pi.build_index(FIXTURE_CORPUS, self.runs, self.knowledge, research_dir=self.tmp / "research")
        self.assertEqual(doc["config"]["k"], 7)
        self.assertNotIn("ignored_key", doc["config"])
        self.assertNotEqual(doc["config_id"], pi.config_id_of(pi.DEFAULT_CONFIG))

    def test_thousand_row_build_within_bound(self):
        corpus_dir = self.tmp / "corpus" / "v1"
        corpus_dir.mkdir(parents=True)
        base = json.loads(FIXTURE_CORPUS.read_text(encoding="utf-8").splitlines()[0])
        with (corpus_dir / "runs.jsonl").open("w", encoding="utf-8") as f:
            for i in range(1000):
                row = dict(base, run_id=f"work_{i:032d}", group_id=f"g_{i:012d}",
                           intent_text=base["intent_text"] + f" variant {i} " + " ".join(f"tok{j}" for j in range(i % 40)))
                f.write(json.dumps(row) + "\n")
        t0 = time.perf_counter()
        doc = pi.build_index(corpus_dir / "runs.jsonl", self.runs, self.knowledge, research_dir=None)
        elapsed = time.perf_counter() - t0
        self.assertEqual(doc["row_count"], 1000)
        self.assertLess(elapsed, 60.0)
        print(f"\n[precursor] 1,000-row index build: {elapsed * 1000:.0f} ms (bound 60 s)")


class SweepLeavesRoutingUntouched(TempDirs):
    @skipUnless(REAL_WORK_DIR.is_dir(), "tracked real work dir absent")
    def test_advise_only_adds_advice_json(self):
        original = self.tmp / "original" / REAL_WORK_DIR.name
        shutil.copytree(REAL_WORK_DIR, original)
        target = self.runs / REAL_WORK_DIR.name
        shutil.copytree(REAL_WORK_DIR, target)
        pi.build_index(FIXTURE_CORPUS, self.tmp / "empty", self.knowledge, research_dir=None)

        proc = subprocess.run([PY, "-m", "tools.workflow.project_precursor", "--advise"], cwd=str(ROOT),
                              env=self.env("shadow"), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["advise"]["advised"], 1)

        new_files = []
        for dirpath, _dirs, files in os.walk(target):
            for name in files:
                rel = Path(dirpath, name).relative_to(target)
                twin = original / rel
                if not twin.exists():
                    new_files.append(str(rel))
                    continue
                self.assertTrue(filecmp.cmp(original / rel, target / rel, shallow=False), f"{rel} changed")
        for dirpath, _dirs, files in os.walk(original):
            for name in files:
                rel = Path(dirpath, name).relative_to(original)
                self.assertTrue((target / rel).exists(), f"{rel} removed")
        self.assertEqual(new_files, [str(Path(pi.ADVICE_DIR, pi.ADVICE_FILE))])

        advice = json.loads((target / pi.ADVICE_DIR / pi.ADVICE_FILE).read_text(encoding="utf-8"))
        self.assertEqual([r["arm"] for r in advice], ["table", "bm25"])
        for r in advice:
            self.assertEqual(r["schema"], pi.ADVICE_SCHEMA)
            self.assertEqual(r["run_id"], REAL_WORK_DIR.name)
            self.assertEqual(r["as_of"], "2026-09-27T18:24:26Z")
            self.assertIn(pi.LATE_DIAGNOSTIC, r["diagnostic"])  # that run already has outcome.final
        manifest = json.loads((target / "work-manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("precursor", manifest)
        self.assertNotIn("precursor", json.dumps(manifest.get("route")))

        # a second sweep is a no-op
        proc = subprocess.run([PY, "-m", "tools.workflow.project_precursor", "--advise"], cwd=str(ROOT),
                              env=self.env("shadow"), capture_output=True, text=True)
        self.assertEqual(json.loads(proc.stdout)["advise"]["advised"], 0)
        self.assertEqual(json.loads(proc.stdout)["advise"]["skipped_has_advice"], 1)


class LateAdviceAndJoin(TempDirs):
    def test_late_is_excluded_and_timely_is_joined(self):
        late = _make_work_dir(self.runs, "work_late" + "0" * 27, "ct stats --json flag in ct/ct.py",
                              dispatched_at="2026-10-01T10:00:00Z", artifact_at="2026-10-01T10:05:00Z",
                              final=("2026-10-01T11:00:00Z", "accepted"))
        timely = _make_work_dir(self.runs, "work_timely" + "0" * 25, "give ct stats a --json flag in ct/ct.py",
                                dispatched_at="2026-10-02T10:00:00Z", artifact_at="2026-10-02T10:05:00Z")
        pending = _make_work_dir(self.runs, "work_pending" + "0" * 24, "night approval grant report",
                                 dispatched_at="2026-10-03T10:00:00Z")
        pi.build_index(FIXTURE_CORPUS, self.runs, self.knowledge, research_dir=None, now=_ts("2026-10-03T12:00:00Z"))
        counts = pi.advise(self.runs, self.knowledge, mode="shadow", now=_ts("2026-10-03T12:00:00Z"))
        self.assertEqual(counts["advised"], 3)
        self.assertEqual(counts["late"], 1)
        self.assertEqual(counts["by_state"].get("ok"), 6)
        self.assertLessEqual(counts["latency_ms_max"], 200.0)

        late_rows = json.loads((late / pi.ADVICE_DIR / pi.ADVICE_FILE).read_text(encoding="utf-8"))
        self.assertTrue(all(pi.LATE_DIAGNOSTIC in r["diagnostic"] for r in late_rows))
        timely_rows = json.loads((timely / pi.ADVICE_DIR / pi.ADVICE_FILE).read_text(encoding="utf-8"))
        self.assertTrue(all(r["diagnostic"] in (None, "table:backend+task_family", "table:backend")
                            for r in timely_rows), timely_rows)

        # the verdict arrives after the advice: append outcome.final later than generated_at
        with (timely / "history.ndjson").open("a", encoding="utf-8") as f:
            f.write(_event(timely.name, "outcome.final", "2026-10-03T13:00:00Z",
                           {"status": "rejected", "validator": "claude-frontier"}, 9) + "\n")
        doc = pi.join(self.runs, self.knowledge, FIXTURE_CORPUS, now=_ts("2026-10-03T14:00:00Z"))
        self.assertEqual(doc["schema"], "precursor-accuracy.v1")
        self.assertEqual(doc["advice_count"], 6)
        self.assertEqual(doc["late_excluded"], 2)
        self.assertEqual(doc["joined_count"], 2)
        self.assertEqual(doc["awaiting_verdict"], 2)
        for arm in ("table", "bm25"):
            period = doc["arms"][arm]["eval"]
            self.assertEqual(period["n"], 1)
            self.assertEqual(period["coverage"], 1.0)
            self.assertIsNotNone(period["brier"])
            self.assertEqual(doc["arms"][arm]["all"]["joined"], 1)
        self.assertTrue((self.knowledge / pi.ACCURACY_FILE).is_file())
        self.assertNotIn(pending.name, json.dumps(doc))

        # a verdict recorded BEFORE generated_at (clock skew or rewrite) is never joined
        with (pending / "history.ndjson").open("a", encoding="utf-8") as f:
            f.write(_event(pending.name, "outcome.final", "2026-10-03T11:00:00Z",
                           {"status": "accepted", "validator": "claude-frontier"}, 9) + "\n")
        doc = pi.join(self.runs, self.knowledge, FIXTURE_CORPUS, now=_ts("2026-10-03T15:00:00Z"))
        self.assertEqual(doc["joined_count"], 2)
        self.assertEqual(doc["awaiting_verdict"], 2)

    def test_entry_runs_all_three_steps_in_shadow(self):
        _make_work_dir(self.runs, "work_e2e" + "0" * 28, "ct stats json", dispatched_at="2026-10-02T10:00:00Z")
        proc = subprocess.run([PY, "-m", "tools.workflow.project_precursor", "--build-index", "--advise", "--join"],
                              cwd=str(ROOT), env=self.env("display"), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["state"], "display")
        self.assertEqual(summary["index"]["row_count"], 12)
        self.assertEqual(summary["advise"]["advised"], 1)
        self.assertEqual(summary["join"]["advice_count"], 2)
        rows = json.loads((self.runs / ("work_e2e" + "0" * 28) / pi.ADVICE_DIR / pi.ADVICE_FILE).read_text())
        self.assertEqual({r["mode"] for r in rows}, {"shadow"}, "display records as shadow in this increment")
