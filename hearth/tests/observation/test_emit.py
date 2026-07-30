from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase
from unittest.mock import patch

from hearth.observation.emit import (
    build_observation,
    builder_id_for,
    emit_dispatch_observation,
    record_dispatch,
    slug,
)
from hearth.observation.identity import DispatchIdentity, current_identity, dispatch_identity

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.workflow.project_capacity import extract_observations  # noqa: E402
from tools.workflow.project_state import read_events  # noqa: E402
from tools.workflow.validate_events import validate_event  # noqa: E402

IDENTITY = DispatchIdentity(caller_id="claude-frontier", runner_class="frontier",
                           node="omen", task_id="task-1", profile="unrestricted")

OLLAMA_SETTINGS = {"node": "omen", "hardware_profile_id": "omen-rtx5070-2026H2"}


def ok_result(**overrides) -> dict:
    result = {
        "ok": True, "text": "a real answer", "model": "qwen3-coder:30b",
        "backend": "omen-ollama", "endpoint": "http://127.0.0.1:11434",
        "routed_by": "default", "occupancy": "available", "error": None,
        "duration_ms": 2000.0, "tokens_in": 1200, "tokens_out": 400,
        "max_tokens": 4096,
    }
    result.update(overrides)
    return result


class ObservationFixture:
    def setUp(self) -> None:
        self.root = Path(mkdtemp()).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _emit(self, result: dict, **overrides) -> dict | None:
        kwargs = dict(result=result, endpoint=result.get("endpoint", ""),
                      backend=result.get("backend"), model=result.get("model"),
                      settings=OLLAMA_SETTINGS, task=None, payload_bytes=4800,
                      resolved_max_tokens=result.get("max_tokens"),
                      identity=IDENTITY, root=self.root)
        kwargs.update(overrides)
        return emit_dispatch_observation(**kwargs)

    def _run_dir(self) -> Path:
        return self.root / "hearth-offload-claude-frontier"

    def _artifacts(self) -> list[Path]:
        return sorted((self._run_dir() / "artifacts").rglob("*.json"))


class IdentityTests(TestCase):
    def test_absent_identity_yields_none(self) -> None:
        self.assertIsNone(current_identity())

    def test_identity_is_pushed_and_reset(self) -> None:
        with dispatch_identity(IDENTITY):
            self.assertEqual(current_identity().caller_id, "claude-frontier")
        self.assertIsNone(current_identity())

    def test_identity_is_reset_even_when_the_body_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            with dispatch_identity(IDENTITY):
                raise RuntimeError("boom")
        self.assertIsNone(current_identity())

    def test_none_does_not_erase_an_outer_identity(self) -> None:
        """A nested call that pushes nothing must inherit, not clear."""
        with dispatch_identity(IDENTITY):
            with dispatch_identity(None):
                self.assertEqual(current_identity().caller_id, "claude-frontier")


class SlugAndBuilderTests(TestCase):
    def test_slug_strips_path_separators(self) -> None:
        """The run id becomes a directory name, and _resolve_artifact_path re-anchors on
        the first 'artifacts/' substring -- so a slash in an identity could redirect
        artifact resolution."""
        self.assertNotIn("/", slug("evil/artifacts/x"))
        self.assertNotIn("\\", slug("evil\\x"))

    def test_slug_never_empty(self) -> None:
        self.assertEqual(slug(""), "unknown")
        self.assertEqual(slug("///"), "unknown")

    def test_builder_id_prefers_the_declared_node(self) -> None:
        self.assertEqual(
            builder_id_for("http://192.168.12.233:8082", "am4-moe", {"node": "am4"}), "am4")

    def test_builder_id_falls_back_to_the_host_map(self) -> None:
        self.assertEqual(builder_id_for("http://127.0.0.1:11434", "omen-ollama", {}), "omen")

    def test_builder_id_falls_back_to_the_bare_host(self) -> None:
        self.assertEqual(
            builder_id_for("http://10.0.0.9:8082", "somewhere", {}), "host:10.0.0.9")

    def test_builder_id_survives_a_pinned_endpoint_with_no_backend(self) -> None:
        """schema minLength is 1: builder_id can never come out empty."""
        for endpoint, settings in (("", None), ("not a url", {}), ("http://", {})):
            self.assertTrue(builder_id_for(endpoint, None, settings))


class EmitSuccessTests(ObservationFixture, TestCase):
    def test_one_artifact_and_one_event_per_dispatch(self) -> None:
        emitted = self._emit(ok_result())

        self.assertTrue(emitted["emitted"])
        self.assertEqual(len(self._artifacts()), 1)
        events = (self._run_dir() / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(events), 1)

    def test_emitted_event_validates_and_carries_artifact_id(self) -> None:
        """The hand-rolled validator ignores artifact_refs and every pre-existing corpus
        event omits artifact_id, but the JSON schema requires it per item. Do not inherit
        that defect."""
        self._emit(ok_result())

        event = json.loads(
            (self._run_dir() / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
        validate_event(event)
        self.assertEqual(len(event["artifact_refs"]), 1)
        for ref in event["artifact_refs"]:
            self.assertTrue(ref["artifact_id"])
            self.assertEqual(ref["artifact_type"], "capacity_observation")
            self.assertTrue(ref["path"])

    def test_observation_is_resolvable_by_the_projector(self) -> None:
        """Round-trip through the real consumer: the artifacts/ substring contract is what
        makes the stored ref path resolvable."""
        self._emit(ok_result())

        events_path = self._run_dir() / "events.jsonl"
        observations, unresolved = extract_observations(read_events(events_path), events_path)

        self.assertEqual(unresolved, 0)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["outcome"], "success")

    def test_field_mapping(self) -> None:
        observation = build_observation(
            result=ok_result(), endpoint="http://127.0.0.1:11434", backend="omen-ollama",
            model="qwen3-coder:30b", settings=OLLAMA_SETTINGS, task=None,
            payload_bytes=4800, resolved_max_tokens=4096, identity=IDENTITY)

        self.assertEqual(observation["contract_version"], "capacity-observation.v1")
        # workflow from the CALLER, builder from the EXECUTOR -- never the same field.
        self.assertEqual(observation["workflow_id"], "wf-hearth-offload-claude-frontier")
        self.assertEqual(observation["builder_id"], "omen")
        self.assertNotEqual(observation["builder_id"], IDENTITY.caller_id)
        self.assertEqual(observation["model_id"], "qwen3-coder:30b")
        self.assertEqual(observation["backend"], "omen-ollama")
        self.assertEqual(observation["hardware_profile_id"], "omen-rtx5070-2026H2")
        self.assertEqual(observation["workload_shape"]["task_kind"], "offload-generate")
        self.assertEqual(observation["workload_shape"]["estimated_context_tokens"], 1200)
        self.assertEqual(observation["observed"]["runtime_s"], 2.0)
        self.assertEqual(observation["observed"]["tokens_per_s"], 200.0)
        self.assertEqual(observation["observed"]["context_tokens"], 1200)
        self.assertEqual(observation["outcome"], "success")

    def test_task_kind_default_is_not_the_ledger_task_class(self) -> None:
        """'inference' is already the kernel ledger's task_class for this tool; sharing the
        string across two bounded contexts invites a join ADR-0010 forbids."""
        observation = build_observation(
            result=ok_result(), endpoint="", backend="omen-ollama", model="m",
            settings={}, task=None, payload_bytes=None, resolved_max_tokens=None,
            identity=IDENTITY)
        self.assertEqual(observation["workload_shape"]["task_kind"], "offload-generate")

    def test_caller_supplied_task_becomes_task_kind(self) -> None:
        observation = build_observation(
            result=ok_result(), endpoint="", backend="omen-ollama", model="m",
            settings={}, task="research", payload_bytes=None, resolved_max_tokens=None,
            identity=IDENTITY)
        self.assertEqual(observation["workload_shape"]["task_kind"], "research")

    def test_derived_and_unmeasured_fields_are_never_invented(self) -> None:
        observation = build_observation(
            result=ok_result(), endpoint="", backend="omen-ollama", model="m",
            settings={}, task=None, payload_bytes=None, resolved_max_tokens=None,
            identity=IDENTITY)
        observed = observation["observed"]
        self.assertIsNone(observed["ttft_s"])        # stream=False, never measured
        self.assertIsNone(observed["ram_gb_peak"])
        self.assertIsNone(observed["vram_gb_peak"])
        self.assertIsNone(observed["physical"])      # model_residency is DERIVED
        self.assertIsNone(observation["decision_id"])
        self.assertIsNone(observation["promotion_status"])

    def test_emitted_observation_is_structurally_valid(self) -> None:
        """Checked against the repo's only observation validator."""
        sys.path.insert(0, str(REPO_ROOT / "tests" / "workflow"))
        from test_capacity_observation_schema import structural_problems

        self._emit(ok_result())
        document = json.loads(self._artifacts()[0].read_text(encoding="utf-8"))

        self.assertEqual(structural_problems(document), [])


class EmitExclusionTests(ObservationFixture, TestCase):
    """ADR-0002: infra-caused failures must not enter the belief layer, and must be
    COUNTED rather than dropped silently."""

    def _exclusions(self) -> list[dict]:
        path = self._run_dir() / "exclusions.ndjson"
        if not path.is_file():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_no_identity_means_no_observation_and_nothing_counted(self) -> None:
        self.assertIsNone(self._emit(ok_result(), identity=None))
        self.assertFalse(self._run_dir().exists())

    def test_ask_path_emits_nothing(self) -> None:
        emitted = self._emit(ok_result(ask=True, backend=None, model=None))
        self.assertFalse(emitted["emitted"])
        self.assertEqual(emitted["excluded"], "not-a-dispatch:ask")
        self.assertEqual(len(self._exclusions()), 1)

    def test_routing_refusal_emits_nothing(self) -> None:
        emitted = self._emit(ok_result(ok=False, error_code="routing_refusal",
                                       error="routing refused"))
        self.assertFalse(emitted["emitted"])
        self.assertIn("routing_refusal", emitted["excluded"])

    def test_infra_failures_are_excluded_and_counted(self) -> None:
        for error, code in (("No connection could be made", "cold_start"),
                            ("Backend busy with 2 tasks", "occupancy_skip"),
                            ("403 Forbidden", "auth_expired"),
                            ("429 RESOURCE_EXHAUSTED", "quota"),
                            ("Expecting value: line 1", "parse_error")):
            emitted = self._emit(ok_result(ok=False, text=None, error=error))
            self.assertFalse(emitted["emitted"], error)
            self.assertEqual(emitted["excluded"], f"infra:{code}", error)
        self.assertEqual(len(self._exclusions()), 5)
        self.assertEqual(self._artifacts(), [])

    def test_unclassified_failure_is_excluded_not_assumed_to_be_evidence(self) -> None:
        emitted = self._emit(ok_result(ok=False, text=None, error="something odd"))
        self.assertEqual(emitted["excluded"], "unclassified:other")

    def test_timeout_is_capability_evidence_not_infra(self) -> None:
        """The rung could not do this workload inside its own declared budget."""
        emitted = self._emit(ok_result(ok=False, text=None, error="Read timed out"))

        self.assertTrue(emitted["emitted"])
        document = json.loads(self._artifacts()[0].read_text(encoding="utf-8"))
        self.assertEqual(document["outcome"], "timeout")
        self.assertEqual(document["failure_class"], "timeout")

    def test_empty_text_with_ok_true_is_a_failure(self) -> None:
        """A real documented pathology: thinking models burning the whole budget on
        hidden reasoning and returning nothing."""
        emitted = self._emit(ok_result(text="   "))

        self.assertTrue(emitted["emitted"])
        document = json.loads(self._artifacts()[0].read_text(encoding="utf-8"))
        self.assertEqual(document["outcome"], "error")
        self.assertEqual(document["failure_class"], "empty_output")

    def test_truncation_at_budget_is_success_with_a_note(self) -> None:
        """Truncation is the caller's budget choice; scoring it as a rung failure would
        poison buckets with caller noise under the unanimous-outcome gate."""
        self._emit(ok_result(tokens_out=4096, max_tokens=4096))

        document = json.loads(self._artifacts()[0].read_text(encoding="utf-8"))
        self.assertEqual(document["outcome"], "success")
        self.assertIn("budget choice", document["workload_shape"]["notes"])


class EmitRobustnessTests(ObservationFixture, TestCase):
    def test_emit_failure_never_breaks_the_dispatch(self) -> None:
        with patch("tools.workflow.fsio.atomic_write_json",
                   side_effect=OSError("disk full")):
            outcome = record_dispatch(
                result=ok_result(), endpoint="http://127.0.0.1:11434",
                backend="omen-ollama", model="qwen3-coder:30b",
                settings=OLLAMA_SETTINGS, task=None, payload_bytes=None,
                resolved_max_tokens=None, identity=IDENTITY, root=self.root)

        self.assertFalse(outcome["emitted"])
        self.assertIn("disk full", outcome["error"])

    def test_two_dispatches_append_rather_than_overwrite(self) -> None:
        self._emit(ok_result())
        self._emit(ok_result(model="qwen2.5:7b-instruct"))

        self.assertEqual(len(self._artifacts()), 2)
        events = (self._run_dir() / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(events), 2)

    def test_artifacts_are_date_sharded(self) -> None:
        self._emit(ok_result())
        artifact = self._artifacts()[0]
        self.assertRegex(artifact.parent.name, r"^\d{4}-\d{2}-\d{2}$")
