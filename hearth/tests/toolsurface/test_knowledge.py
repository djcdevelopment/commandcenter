from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.toolsurface.knowledge import (
    project,
    project_capacity_knowledge,
    project_offload_knowledge,
    query_beliefs_summary,
    query_capabilities,
    query_capacity,
    query_findings,
    query_offload,
    record_event,
)
from hearth.toolsurface.knowledge import _restamp_written_file as _knowledge_restamp
from hearth.projection.rebuild import (
    EXPECTED_FILES as _REBUILD_EXPECTED_FILES,
    RebuildValidationError,
    rebuild_knowledge,
)
from hearth.projection.rebuild import _restamp_written_file as _rebuild_restamp
from hearth.projection.rebuild import _validate_staged as _rebuild_validate_staged
from tools.workflow.corpus import Corpus
from tools.workflow.corpus_guard import CorpusRegressionError

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_RUNS = REPO_ROOT / "fixtures" / "workflow" / "runs"
SINGLE_EVENT = REPO_ROOT / "fixtures" / "workflow" / "single-event.question-answered.json"


class KnowledgeScopedTestCase(TestCase):
    """Every test runs against a temp sandbox — the real knowledge/ is never written.

    Fixture run trees (events + artifact refs) are copied into the sandbox, which also
    strips the 'fixtures' path component so the fixture-taint guard judges these sources
    exactly as it would judge real ones aimed at a non-repo out dir: pass.
    """

    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous_scope = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)
        shutil.copytree(FIXTURE_RUNS, self.scope / "runs")

    def tearDown(self) -> None:
        if self._previous_scope is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous_scope
        shutil.rmtree(self.scope, ignore_errors=True)

    def _write_capacity_observation_run(
        self, run_id: str, builder_id: str, model_id: str, backend: str,
        outcome: str, observation_id: str, timestamp: str,
    ) -> None:
        """Add one more capacity_observation (event + artifact) into the sandbox's
        runs/ tree, on top of whatever FIXTURE_RUNS already copied in. Kept minimal:
        extract_observations only reads artifact_refs of type capacity_observation,
        and read_events does no schema validation (that only happens on append), so
        a bare-bones event dict is sufficient here."""
        run_dir = self.scope / "runs" / run_id
        obs_dir = run_dir / "artifacts" / "observations"
        obs_dir.mkdir(parents=True, exist_ok=True)
        observation = {
            "contract_version": "capacity-observation.v1",
            "observation_id": observation_id,
            "decision_id": f"dec_{observation_id}",
            "workflow_id": f"wf_{run_id}",
            "run_id": run_id,
            "timestamp": timestamp,
            "builder_id": builder_id,
            "model_id": model_id,
            "backend": backend,
            "workload_shape": {"task_kind": "build", "estimated_context_tokens": 4096,
                               "requires_gpu": False, "notes": None},
            "observed": {"runtime_s": 100.0, "ttft_s": None, "tokens_per_s": None,
                        "ram_gb_peak": None, "vram_gb_peak": None, "context_tokens": None},
            "outcome": outcome,
            "failure_class": None if outcome == "success" else "synthetic_failure",
            "promotion_status": "approved" if outcome == "success" else None,
        }
        (obs_dir / f"{observation_id}.json").write_text(
            json.dumps(observation), encoding="utf-8")

        event = {
            "event_id": f"evt_{observation_id}",
            "event_type": "promotion.approved" if outcome == "success" else "retrospective.created",
            "timestamp": timestamp,
            "workflow_id": f"wf_{run_id}",
            "run_id": run_id,
            "artifact_refs": [
                {"artifact_id": f"art_{observation_id}", "artifact_type": "capacity_observation",
                 "path": f"runs/{run_id}/artifacts/observations/{observation_id}.json"},
            ],
        }
        events_path = run_dir / "events.jsonl"
        with events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event) + "\n")


class RecordEventTests(KnowledgeScopedTestCase):
    def test_valid_event_appends_via_existing_machinery(self) -> None:
        event = json.loads(SINGLE_EVENT.read_text(encoding="utf-8"))
        result = record_event(event, events_path="runs/hearth/events.jsonl")
        self.assertTrue(result["appended"])
        self.assertEqual(result["event_type"], "question.answered")

        ledger = self.scope / "runs" / "hearth" / "events.jsonl"
        lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["event_id"], event["event_id"])

    def test_invalid_event_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            record_event({"event_type": "not.a.real.type"})

    def test_events_path_outside_scope_rejected(self) -> None:
        event = json.loads(SINGLE_EVENT.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            record_event(event, events_path="../escape.jsonl")


class ProjectTests(KnowledgeScopedTestCase):
    def test_capacity_and_findings_project_from_fixture_corpus(self) -> None:
        result = project(kinds=["capacity", "findings"])
        self.assertTrue(result["ok"], result)
        self.assertGreater(result["event_files"], 0)

        knowledge_dir = self.scope / "knowledge"
        self.assertTrue((knowledge_dir / "capacity_estimates.json").is_file())
        self.assertTrue((knowledge_dir / "findings.json").is_file())

        findings_summary = result["kinds"]["findings"]["summary"]
        self.assertGreater(findings_summary["observation_count"], 0)

    def test_project_all_runs_every_kind_in_dependency_order(self) -> None:
        result = project()
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            list(result["kinds"].keys()),
            ["capacity", "findings", "associations", "coverage", "experiments", "policy"],
        )
        for file_name in ("capabilities.json", "coverage.json", "policy.json",
                          "experiment_candidates.json"):
            self.assertTrue((self.scope / "knowledge" / file_name).is_file(), file_name)

    def test_unknown_kind_rejected(self) -> None:
        with self.assertRaises(ValueError):
            project(kinds=["telemetry"])

    def test_out_dir_outside_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            project(kinds=["capacity"], out="../leaky-knowledge")

    def test_projector_fault_is_reported_not_raised(self) -> None:
        # policy without a findings.json upstream: the per-kind digest carries the error
        result = project(kinds=["policy"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["kinds"]["policy"]["ok"])
        self.assertTrue(result["kinds"]["policy"]["error"])


class QueryTests(KnowledgeScopedTestCase):
    def test_queries_before_projection_report_unavailable(self) -> None:
        self.assertFalse(query_capabilities()["available"])
        self.assertFalse(query_findings()["available"])

    def test_queries_return_content_and_mtime_after_projection(self) -> None:
        project(kinds=["capacity", "findings", "associations"])

        capabilities = query_capabilities()
        self.assertTrue(capabilities["available"])
        self.assertIsNotNone(capabilities["mtime"])
        self.assertEqual(capabilities["content"]["contract_version"], "capabilities.v1")

        findings = query_findings()
        self.assertTrue(findings["available"])
        self.assertGreater(len(findings["content"]["findings"]), 0)

    def test_beliefs_summary_is_a_digest_with_mtimes(self) -> None:
        project(kinds=["capacity", "findings", "associations"])
        summary = query_beliefs_summary()
        files = summary["files"]
        self.assertTrue(files["capabilities.json"]["available"])
        self.assertIn("capability_count", files["capabilities.json"])
        self.assertIsNotNone(files["findings.json"]["mtime"])
        self.assertIn("finding_counts", files["findings.json"])
        self.assertFalse(files["policy.json"]["available"])  # not projected in this test


class CapacityKnowledgeTests(KnowledgeScopedTestCase):
    """JS2: project_capacity_knowledge / query_capacity round-trip via the ledger."""

    def _write_ledger(self, relative_path: str = "hearth/var/ledger/events.ndjson") -> Path:
        ledger = self.scope / relative_path
        ledger.parent.mkdir(parents=True, exist_ok=True)
        events = [
            {
                "schema": "hearth-event.v1",
                "event_id": "he_a",
                "ts": "2026-07-04T00:00:00+00:00",
                "caller": {"id": "local-1", "runner_class": "local", "node": "omen"},
                "tool": "run_tests",
                "ok": True,
                "duration_ms": 1000,
                "cost": {"tokens_in": 10, "tokens_out": 50, "watt_s": None},
                "task_id": None,
            },
        ]
        with ledger.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")
        return ledger

    def test_query_before_projection_reports_unavailable(self) -> None:
        self.assertFalse(query_capacity()["available"])

    def test_project_then_query_round_trips_capacity_json(self) -> None:
        self._write_ledger()
        result = project_capacity_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        self.assertEqual(result["bucket_count"], 1)
        self.assertEqual(result["evidence_watermark"], "2026-07-04T00:00:00+00:00")

        queried = query_capacity()
        self.assertTrue(queried["available"])
        self.assertIsNotNone(queried["mtime"])
        content = queried["content"]
        self.assertEqual(content["contract_version"], "capacity.v1")
        self.assertEqual(len(content["buckets"]), 1)
        self.assertEqual(content["buckets"][0]["tool"], "run_tests")

    def test_missing_ledger_yields_empty_document(self) -> None:
        result = project_capacity_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        self.assertEqual(result["bucket_count"], 0)
        self.assertEqual(result["evidence_watermark"], None)

    def test_ledger_path_outside_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            project_capacity_knowledge(ledger_path="../escape.ndjson")

    def test_regression_over_capacity_json_is_refused_by_guard(self) -> None:
        """CQRS/ES plan step 2: capacity.json now goes through corpus_guard like every other
        knowledge file — a re-projection over a ledger with fewer buckets (and an older
        watermark) than what's already on disk must be refused, not silently clobbered."""
        big_ledger = self._write_ledger("hearth/var/ledger/events.ndjson")
        project_capacity_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        before = (self.scope / "knowledge" / "capacity.json").read_text(encoding="utf-8")

        # Overwrite the ledger with an empty one: 0 buckets, null watermark -> regression.
        big_ledger.write_text("", encoding="utf-8")
        with self.assertRaises(CorpusRegressionError):
            project_capacity_knowledge(ledger_path="hearth/var/ledger/events.ndjson")

        after = (self.scope / "knowledge" / "capacity.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)  # blocked write leaves the on-disk file untouched

    def test_forward_capacity_projection_still_succeeds_under_guard(self) -> None:
        """Normal advance (more evidence) must still pass now that the guard is wired in."""
        ledger = self._write_ledger("hearth/var/ledger/events.ndjson")
        project_capacity_knowledge(ledger_path="hearth/var/ledger/events.ndjson")

        with ledger.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({
                "schema": "hearth-event.v1",
                "event_id": "he_b",
                "ts": "2026-07-04T01:00:00+00:00",
                "caller": {"id": "local-1", "runner_class": "local", "node": "omen"},
                "tool": "local_generate",
                "ok": True,
                "duration_ms": 500,
                "cost": {"tokens_in": 5, "tokens_out": 25, "watt_s": None},
                "task_id": None,
            }) + "\n")

        result = project_capacity_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        self.assertEqual(result["bucket_count"], 2)
        self.assertEqual(result["evidence_watermark"], "2026-07-04T01:00:00+00:00")


class CorpusProvenanceTests(KnowledgeScopedTestCase):
    """CQRS/ES step 4: every materialized doc is stamped with the corpus that produced it."""

    def test_project_docs_carry_corpus_digest_and_event_count(self) -> None:
        result = project(kinds=["capacity", "findings"])
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["corpus_digest"].startswith("sha256:"))
        self.assertGreater(result["corpus_event_count"], 0)

        for file_name in ("capacity_estimates.json", "findings.json",
                          "known_good_models.json", "prediction_accuracy.json"):
            document = json.loads(
                (self.scope / "knowledge" / file_name).read_text(encoding="utf-8-sig"))
            self.assertEqual(document["corpus_digest"], result["corpus_digest"], file_name)
            self.assertEqual(document["corpus_event_count"], result["corpus_event_count"], file_name)

    def test_rerun_over_identical_corpus_yields_identical_digest(self) -> None:
        first = project(kinds=["capacity"])
        second = project(kinds=["capacity"])
        self.assertTrue(first["ok"] and second["ok"])
        self.assertEqual(first["corpus_digest"], second["corpus_digest"])
        self.assertEqual(first["corpus_event_count"], second["corpus_event_count"])

        document = json.loads(
            (self.scope / "knowledge" / "capacity_estimates.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(document["corpus_digest"], first["corpus_digest"])

    def test_capacity_json_carries_ledger_corpus_provenance(self) -> None:
        ledger = self.scope / "hearth" / "var" / "ledger" / "events.ndjson"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        events = [
            {"ts": "2026-07-04T00:00:00+00:00", "tool": "run_tests", "ok": True,
             "duration_ms": 1000, "caller": {"node": "omen"}},
            {"ts": "2026-07-04T00:01:00+00:00", "tool": "run_tests", "ok": True,
             "duration_ms": 1200, "caller": {"node": "omen"}},
        ]
        with ledger.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")

        project_capacity_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        content = query_capacity()["content"]
        self.assertEqual(content["corpus_event_count"], 2)
        self.assertTrue(content["corpus_digest"].startswith("sha256:"))

        # identical ledger -> identical digest on re-projection
        digest_before = content["corpus_digest"]
        project_capacity_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        self.assertEqual(query_capacity()["content"]["corpus_digest"], digest_before)


class OffloadKnowledgeTests(KnowledgeScopedTestCase):
    """Mirror CapacityKnowledgeTests for offload.json (S2 executor economics)."""

    def _write_ledger(self, relative_path: str = "hearth/var/ledger/events.ndjson") -> Path:
        ledger = self.scope / relative_path
        ledger.parent.mkdir(parents=True, exist_ok=True)
        events = [
            {
                "schema": "hearth-event.v1",
                "event_id": "he_a",
                "ts": "2026-07-04T00:00:00+00:00",
                "caller": {"id": "local-1", "runner_class": "local", "node": "omen"},
                "tool": "local_generate",
                "task_class": "inference",
                "backend": "omen-ollama",
                "model": "qwen2",
                "ok": True,
                "duration_ms": 1000,
                "cost": {"tokens_in": 10, "tokens_out": 50, "watt_s": None},
                "task_id": None,
            },
        ]
        with ledger.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")
        return ledger

    def test_query_before_projection_reports_unavailable(self) -> None:
        self.assertFalse(query_offload()["available"])

    def test_project_then_query_round_trips_offload_json(self) -> None:
        self._write_ledger()
        result = project_offload_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        self.assertEqual(result["bucket_count"], 1)
        self.assertEqual(result["evidence_watermark"], "2026-07-04T00:00:00+00:00")

        queried = query_offload()
        self.assertTrue(queried["available"])
        self.assertIsNotNone(queried["mtime"])
        content = queried["content"]
        self.assertEqual(content["contract_version"], "offload.v1")
        self.assertEqual(len(content["buckets"]), 1)
        self.assertEqual(content["buckets"][0]["backend"], "omen-ollama")

    def test_regression_over_offload_json_is_refused_by_guard(self) -> None:
        big_ledger = self._write_ledger("hearth/var/ledger/events.ndjson")
        project_offload_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        before = (self.scope / "knowledge" / "offload.json").read_text(encoding="utf-8")

        big_ledger.write_text("", encoding="utf-8")
        with self.assertRaises(CorpusRegressionError):
            project_offload_knowledge(ledger_path="hearth/var/ledger/events.ndjson")

        after = (self.scope / "knowledge" / "offload.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)


class ModelGuardListTests(KnowledgeScopedTestCase):
    """S2 (scheduler-lane strategy) / DECISIONS-PENDING 2026-07-03 (DECISION-NEEDED-A2):
    guard-test coverage for knowledge/known_good_models.json and known_bad_models.json,
    across the two consumers that materialize them — hearth.toolsurface.knowledge.project()
    and hearth.projection.rebuild.rebuild_knowledge(). FIXTURE_RUNS already carries one
    known-bad combo (claudefarm1|qwen3-30b-a3b-awq|vllm, 3/3 moe_offload_crash, zero
    successes) and several known-good combos (e.g. omen-worker-1|qwen3-coder:30b|ollama,
    a single successful run) — see tools/workflow/project_capacity.classify_known_good/
    classify_known_bad for the actual thresholds (>=1 success & rate>=0.7 vs. zero
    successes & >=2 failures).
    """

    KNOWN_BAD_COMBO = ("claudefarm1", "qwen3-30b-a3b-awq", "vllm")
    KNOWN_GOOD_COMBO = ("omen-worker-1", "qwen3-coder:30b", "ollama")

    DUAL_MODEL_ID = "dual-track:9b"
    DUAL_GOOD_COMBO = ("dual-good-builder", DUAL_MODEL_ID, "ollama")
    DUAL_BAD_COMBO = ("dual-bad-builder", DUAL_MODEL_ID, "vllm")

    @staticmethod
    def _combo_ids(document: dict) -> set[tuple[str, str, str]]:
        return {(e["builder_id"], e["model_id"], e["backend"]) for e in document["entries"]}

    def _read_lists(self, knowledge_dir: Path) -> tuple[dict, dict]:
        good = json.loads((knowledge_dir / "known_good_models.json").read_text(encoding="utf-8-sig"))
        bad = json.loads((knowledge_dir / "known_bad_models.json").read_text(encoding="utf-8-sig"))
        return good, bad

    def _assert_bad_rejected_and_good_passes(self, knowledge_dir: Path) -> None:
        good, bad = self._read_lists(knowledge_dir)
        good_ids, bad_ids = self._combo_ids(good), self._combo_ids(bad)
        self.assertIn(self.KNOWN_BAD_COMBO, bad_ids)
        self.assertNotIn(self.KNOWN_BAD_COMBO, good_ids)
        self.assertIn(self.KNOWN_GOOD_COMBO, good_ids)
        self.assertNotIn(self.KNOWN_GOOD_COMBO, bad_ids)

    def test_known_bad_rejected_and_known_good_passes_via_project(self) -> None:
        """Requirement 1+2, consumer #1 (hearth.toolsurface.knowledge.project): the
        vllm/AWQ/MoE combo is rejected into known_bad (and absent from known_good);
        the omen ollama combo passes into known_good (and absent from known_bad)."""
        result = project(kinds=["capacity"])
        self.assertTrue(result["ok"], result)
        self._assert_bad_rejected_and_good_passes(self.scope / "knowledge")

    def test_known_bad_rejected_and_known_good_passes_via_rebuild(self) -> None:
        """Requirement 1+2, consumer #2 (hearth.projection.rebuild.rebuild_knowledge):
        same classification must hold after a from-zero rebuild, not just an
        incremental project() call."""
        result = rebuild_knowledge()
        self.assertTrue(result["ok"], result)
        self._assert_bad_rejected_and_good_passes(self.scope / "knowledge")

    def _seed_dual_track_model(self) -> None:
        # Same model_id, two different (builder, backend) combos: one earns known_good,
        # the other earns known_bad. classify_known_good/classify_known_bad operate per
        # combo (builder_id|model_id|backend), so this is the realistic way the same
        # model_id ends up represented on BOTH lists at once.
        self._write_capacity_observation_run(
            "run_dual_good_001", "dual-good-builder", self.DUAL_MODEL_ID, "ollama",
            "success", "obs_dual_good_1", "2026-07-06T00:00:00Z")
        self._write_capacity_observation_run(
            "run_dual_bad_001", "dual-bad-builder", self.DUAL_MODEL_ID, "vllm",
            "oom_crash", "obs_dual_bad_1", "2026-07-06T01:00:00Z")
        self._write_capacity_observation_run(
            "run_dual_bad_002", "dual-bad-builder", self.DUAL_MODEL_ID, "vllm",
            "oom_crash", "obs_dual_bad_2", "2026-07-06T02:00:00Z")

    def _assert_model_on_both_lists_stays_allowed(self, knowledge_dir: Path) -> None:
        good, bad = self._read_lists(knowledge_dir)
        good_ids, bad_ids = self._combo_ids(good), self._combo_ids(bad)
        # precondition: the model_id genuinely appears on both lists (different combos)
        self.assertIn(self.DUAL_GOOD_COMBO, good_ids)
        self.assertIn(self.DUAL_BAD_COMBO, bad_ids)
        # ADR-0003 principle ("an explicit allow overrides an exclusion"), applied here
        # by analogy: the model's known_good combo entry must survive fully intact —
        # a known_bad classification on a DIFFERENT combo must not evict or suppress it.
        good_entry = next(e for e in good["entries"] if e["model_id"] == self.DUAL_MODEL_ID)
        self.assertEqual(good_entry["builder_id"], "dual-good-builder")
        self.assertEqual(good_entry["backend"], "ollama")
        self.assertGreaterEqual(good_entry["success_rate"], 0.7)

    def test_model_on_both_lists_keeps_its_known_good_entry_via_project(self) -> None:
        """Requirement 3 (ADR-0003 pin), consumer #1: a model_id present on known_bad
        (via one combo) must not lose its known_good entry (via another combo) —
        the allow-list entry is treated as still-allowed, not overridden."""
        self._seed_dual_track_model()
        result = project(kinds=["capacity"])
        self.assertTrue(result["ok"], result)
        self._assert_model_on_both_lists_stays_allowed(self.scope / "knowledge")

    def test_model_on_both_lists_keeps_its_known_good_entry_via_rebuild(self) -> None:
        """Requirement 3 (ADR-0003 pin), consumer #2: same pin, through a from-zero
        rebuild rather than an incremental project() call."""
        self._seed_dual_track_model()
        result = rebuild_knowledge()
        self.assertTrue(result["ok"], result)
        self._assert_model_on_both_lists_stays_allowed(self.scope / "knowledge")


class ModelGuardListQueryFailLoudTests(KnowledgeScopedTestCase):
    """Requirement 4 (malformed/missing lists), read side: query_beliefs_summary is the
    one public function in hearth.toolsurface.knowledge that touches known_good_models.json
    / known_bad_models.json outside of writing them. Missing files are a documented,
    legitimate "not yet projected" state (available: False); malformed JSON on disk is
    NOT swallowed — json.loads raises straight through to the caller. Both halves of
    this contract are pinned explicitly below."""

    def test_beliefs_summary_reports_both_lists_unavailable_before_projection(self) -> None:
        files = query_beliefs_summary()["files"]
        self.assertFalse(files["known_good_models.json"]["available"])
        self.assertFalse(files["known_bad_models.json"]["available"])

    def test_beliefs_summary_raises_loud_on_malformed_known_good_json(self) -> None:
        knowledge_dir = self.scope / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        (knowledge_dir / "known_good_models.json").write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            query_beliefs_summary()

    def test_beliefs_summary_raises_loud_on_malformed_known_bad_json(self) -> None:
        knowledge_dir = self.scope / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        (knowledge_dir / "known_bad_models.json").write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            query_beliefs_summary()


class ModelGuardListRestampSilentSwallowTests(KnowledgeScopedTestCase):
    """Requirement 4 (malformed/missing lists), write side: after materialize_knowledge
    writes known_good_models.json / known_bad_models.json, BOTH consumers restamp them
    with corpus provenance via a private `_restamp_written_file` helper (one copy in
    hearth/toolsurface/knowledge.py, one copy in hearth/projection/rebuild.py — same
    logic, not shared). This is a FINDING, documented here rather than changed: that
    helper silently no-ops (no exception, no signal of any kind) when the file is
    missing, or when it parses as JSON but the top-level value is not a dict. Under
    today's real materialize_knowledge, these two files are always well-formed dicts,
    so the silent branches are dead in practice — but they are silent-by-construction,
    not fail-loud, which is exactly the gap DECISION-NEEDED-A2.md flags for these two
    files (neither carries a watermark nor a count, so they are also the two files
    left OUT of corpus_guard's regression protection). Syntactically invalid JSON, by
    contrast, is NOT swallowed: json.loads raises straight through, uncaught.

    In hearth/projection/rebuild.py specifically, this silent-swallow branch is masked
    downstream: _validate_staged() re-checks dict-ness + contract_version on every
    staged file before the atomic swap, so a non-dict known_good/known_bad document
    cannot actually reach the live knowledge/ dir via rebuild_knowledge() even though
    the restamp step itself stayed silent. hearth/toolsurface/knowledge.py's project()
    has no equivalent downstream check — the restamp step is the last word there.
    """

    @staticmethod
    def _corpus() -> Corpus:
        return Corpus(root=Path("."), event_files=(), event_count=3, watermark=None,
                      corpus_digest="sha256:deadbeef")

    def test_project_restamp_silently_skips_missing_file(self) -> None:
        missing = self.scope / "knowledge" / "known_bad_models.json"
        self.assertFalse(missing.exists())
        _knowledge_restamp(missing, self._corpus())  # no exception -> silent no-op
        self.assertFalse(missing.exists())  # still nothing on disk; no error surfaced either

    def test_project_restamp_silently_skips_non_dict_document(self) -> None:
        target = self.scope / "knowledge" / "known_good_models.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        _knowledge_restamp(target, self._corpus())  # SILENT SWALLOW: no exception raised
        # file is left byte-for-byte as written -- no corpus_digest was, or could be, added
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), ["not", "a", "dict"])

    def test_project_restamp_raises_on_syntactically_invalid_json(self) -> None:
        target = self.scope / "knowledge" / "known_bad_models.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            _knowledge_restamp(target, self._corpus())

    def test_rebuild_restamp_silently_skips_non_dict_document(self) -> None:
        target = self.scope / "staging-fixture" / "known_good_models.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(42), encoding="utf-8")
        _rebuild_restamp(target, self._corpus())  # same silent swallow as knowledge.py's copy
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), 42)

    def test_rebuild_validate_staged_rejects_non_dict_known_good_file(self) -> None:
        """The downstream safety net referenced above: _validate_staged catches what
        the restamp step silently let through, so rebuild_knowledge() as a whole still
        fails loud (RebuildValidationError) before anything touches the live dir."""
        staging_dir = self.scope / "staging-fixture-2"
        staging_dir.mkdir(parents=True, exist_ok=True)
        for name in _REBUILD_EXPECTED_FILES:
            (staging_dir / name).write_text(json.dumps({"contract_version": "x.v1"}), encoding="utf-8")
        (staging_dir / "known_good_models.json").write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        with self.assertRaises(RebuildValidationError):
            _rebuild_validate_staged(staging_dir)
