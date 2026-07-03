from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.project_associations import materialize_associations
from tools.workflow.project_capacity import collect_event_files
from tools.workflow.reference_runner import (
    CANDIDATE_POOLS,
    load_capabilities,
    run_reference_workflow,
    schedule,
)
from tools.workflow.project_state import read_events
from tools.workflow.validate_events import validate_file

ROOT = Path(__file__).resolve().parents[2]
RUNS_FIXTURE_DIR = ROOT / "fixtures" / "workflow" / "runs"
WORK_ITEM = ROOT / "fixtures" / "workflow" / "sample-work-item.md"
DECISION_SCHEMA = json.loads((ROOT / "contracts" / "scheduler-decision.v1.schema.json").read_text(encoding="utf-8"))

BUILD_OLLAMA_CAPABILITY = "capability:task_kind=build|backend=ollama"


class CapabilityDirectedDispatchTests(TestCase):
    """S7: the scheduler asks which capabilities the work requires (derived from trace history),
    then which candidate satisfies them — qualified capabilities are scheduled, not machines."""

    def setUp(self) -> None:
        self.temp_dir = Path(mkdtemp())
        self.knowledge_dir = self.temp_dir / "knowledge"
        materialize_associations(collect_event_files([RUNS_FIXTURE_DIR]), self.knowledge_dir)
        self.capabilities_path = self.knowledge_dir / "capabilities.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run(self) -> Path:
        run_reference_workflow(WORK_ITEM, self.temp_dir, "capability-directed",
                               capabilities_path=self.capabilities_path)
        return self.temp_dir / WORK_ITEM.stem

    def _decision(self, run_dir: Path) -> dict:
        return json.loads((run_dir / "artifacts" / "decisions" / "dec_assign_001.json")
                          .read_text(encoding="utf-8"))

    def test_qualified_capability_outranks_a_higher_base_score(self) -> None:
        decision = self._decision(self._run())
        # local candidate came in at base 0.9; the qualified ollama candidate won on 0.8 + 0.15
        self.assertEqual(decision["selected"]["builder_id"], "omen-worker-1")
        scores = {entry["builder_id"]: entry["score"] for entry in decision["candidates_considered"]}
        self.assertEqual(scores["omen-worker-1"], 0.95)
        self.assertEqual(scores["builder-1"], 0.9)

    def test_dispatch_explains_the_capability_match(self) -> None:
        decision = self._decision(self._run())
        influence = decision["capability_influence"]
        self.assertEqual(influence["capabilities_required"], [BUILD_OLLAMA_CAPABILITY])
        self.assertEqual(influence["capabilities_matched"], [BUILD_OLLAMA_CAPABILITY])
        self.assertEqual(influence["capabilities_missing"], [])
        self.assertIn("derived from trace history", influence["requirements_source"])
        self.assertIn("qualified capabilities are scheduled, not machines", decision["decision_reason"])
        unqualified = next(entry for entry in influence["candidates"]
                           if entry["builder_id"] == "builder-1")
        self.assertEqual(unqualified["matched"], [])
        self.assertEqual(unqualified["missing"], [BUILD_OLLAMA_CAPABILITY])

    def test_capability_influence_conforms_to_contract(self) -> None:
        decision = self._decision(self._run())
        self.assertLessEqual(set(decision), set(DECISION_SCHEMA["properties"]))
        block_schema = DECISION_SCHEMA["properties"]["capability_influence"]
        influence = decision["capability_influence"]
        self.assertLessEqual(set(block_schema["required"]), set(influence))
        self.assertLessEqual(set(influence), set(block_schema["properties"]))
        for entry in influence["candidates"]:
            self.assertLessEqual(set(entry), set(block_schema["properties"]["candidates"]["items"]["properties"]))

    def test_events_stay_valid_and_run_completes(self) -> None:
        run_dir = self._run()
        self.assertEqual(validate_file(run_dir / "events.jsonl"), [])
        events = read_events(run_dir / "events.jsonl")
        self.assertIn("promotion.approved", {event["event_type"] for event in events})

    def test_without_capabilities_the_influence_is_null(self) -> None:
        run_reference_workflow(WORK_ITEM, self.temp_dir, "happy")
        decision = self._decision(self.temp_dir / WORK_ITEM.stem)
        self.assertIsNone(decision["capability_influence"])

    def test_scenario_requires_capabilities(self) -> None:
        with self.assertRaises(ValueError):
            run_reference_workflow(WORK_ITEM, self.temp_dir, "capability-directed")

    def test_stale_qualification_does_not_satisfy_a_requirement(self) -> None:
        capabilities = load_capabilities(self.capabilities_path)
        capability = next(c for c in capabilities if c["capability_id"] == BUILD_OLLAMA_CAPABILITY)
        capability["qualification_status"] = "requalification_due"
        selection = schedule(CANDIDATE_POOLS["capability-pool"], "build", [],
                             capabilities=capabilities)
        # with the qualification stale, nothing matches and the higher base score wins again
        self.assertEqual(selection["selected"]["builder_id"], "builder-1")
        self.assertEqual(selection["capability_influence"]["capabilities_matched"], [])

    def test_outside_the_measured_envelope_is_not_qualified_ground(self) -> None:
        capabilities = load_capabilities(self.capabilities_path)
        selection = schedule(CANDIDATE_POOLS["capability-pool"], "build", [],
                             capabilities=capabilities, estimated_context_tokens=32768)
        # 32768 exceeds the observed max_context_tokens of 16384: untested, so unmatched
        self.assertEqual(selection["capability_influence"]["capabilities_matched"], [])
        self.assertEqual(selection["selected"]["builder_id"], "builder-1")
