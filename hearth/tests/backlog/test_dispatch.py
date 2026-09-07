"""hearth.backlog.dispatch — the corpus records a drain dispatch leaves behind.

Every document this module builds is validated against the CONTRACT it claims
(``contracts/*.schema.json``, `jsonschema`) rather than against a hand-copied
list of keys, and the one constant that mirrors a schema enum
(``PLAN_EXPERIMENT_TYPES``) is compared to the schema itself, so the pair cannot
drift apart silently.

Nothing here touches the live corpus: every path is a temp root.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

import jsonschema

from hearth.backlog import dispatch
from hearth.backlog.briefs import Brief
from tools.workflow.validate_events import ValidationError, validate_event, validate_file

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS = _REPO_ROOT / "contracts"


def _schema(name: str) -> dict:
    return json.loads((_CONTRACTS / name).read_text(encoding="utf-8"))


def _brief(source: str = "candidate", source_ref: str = "bbb_high",
           slug: str = "bbb_high", task_class: str = "proofing") -> Brief:
    return Brief(slug=slug, title="t", body="a body", builders=None,
                 task_class=task_class, est_tokens=None, requires=(),
                 max_age_s=None, source=source, source_ref=source_ref)


class IdentityTests(TestCase):
    def test_experiment_id_is_the_candidate_id_verbatim(self) -> None:
        # The whole suppression loop rests on this equality: an
        # experiment-result.v1 row keyed with anything else would never be
        # matched by sources.already_run_ids.
        candidate_id = "backend_comparison:qwen2.5:14b:ollama-cuda+ollama-vulkan"
        brief = _brief(source_ref=candidate_id, slug="backend_comparison-qwen2.5-14b")
        self.assertEqual(dispatch.experiment_id_for(brief), candidate_id)

    def test_authored_and_refined_ids_are_namespaced(self) -> None:
        self.assertEqual(
            dispatch.experiment_id_for(_brief("authored", "0001-thing.md", "0001-thing")),
            "authored:0001-thing")
        self.assertEqual(
            dispatch.experiment_id_for(_brief("refined", "refine-alpha-1", "refine-alpha-1")),
            "refined:refine-alpha-1")

    def test_a_namespaced_id_can_never_collide_with_a_candidate_id(self) -> None:
        # Candidate ids are "<experiment_type>:<combo>"; neither namespace is an
        # experiment type, so an authored brief can never suppress a candidate.
        self.assertNotIn("authored", dispatch.PLAN_EXPERIMENT_TYPES)
        self.assertNotIn("refined", dispatch.PLAN_EXPERIMENT_TYPES)

    def test_experiment_type_comes_from_the_candidate_id_prefix(self) -> None:
        for kind in sorted(dispatch.PLAN_EXPERIMENT_TYPES):
            with self.subTest(kind=kind):
                brief = _brief(source_ref=f"{kind}:some|combo|here", slug="c")
                self.assertEqual(dispatch.experiment_type_for(brief), kind)

    def test_an_unknown_prefix_falls_back_and_never_invents_an_enum_member(self) -> None:
        for source_ref in ("bbb_high", "confidence_calibration:corpus", "nope:x"):
            with self.subTest(source_ref=source_ref):
                brief = _brief(source_ref=source_ref, slug="c")
                self.assertEqual(dispatch.experiment_type_for(brief),
                                 dispatch.DEFAULT_EXPERIMENT_TYPE)
        # confidence_calibration is a real candidate type in
        # project_experiments.EXPERIMENT_TYPES but NOT in the plan contract --
        # the exact case that would otherwise write an invalid plan.
        from tools.workflow.project_experiments import EXPERIMENT_TYPES
        self.assertIn("confidence_calibration", EXPERIMENT_TYPES)
        self.assertNotIn("confidence_calibration", dispatch.PLAN_EXPERIMENT_TYPES)

    def test_authored_and_refined_briefs_use_the_fallback_type(self) -> None:
        self.assertEqual(dispatch.experiment_type_for(_brief("authored", "a.md", "a")),
                         dispatch.DEFAULT_EXPERIMENT_TYPE)
        self.assertEqual(dispatch.experiment_type_for(_brief("refined", "r", "r")),
                         dispatch.DEFAULT_EXPERIMENT_TYPE)

    def test_the_plan_type_constant_matches_the_contract_enum(self) -> None:
        enum = set(_schema("experiment-plan.v1.schema.json")
                   ["properties"]["experiment_type"]["enum"])
        self.assertEqual(set(dispatch.PLAN_EXPERIMENT_TYPES), enum)
        self.assertIn(dispatch.DEFAULT_EXPERIMENT_TYPE, enum)

    def test_a_dispatch_id_is_unique_and_shaped_like_a_plan_id(self) -> None:
        brief = _brief()
        ids = {dispatch.new_dispatch_id(brief) for _ in range(20)}
        self.assertEqual(len(ids), 20, "a dispatch id must never collide")
        for value in ids:
            self.assertTrue(value.startswith("hearth-"))
            self.assertIn("bbb", value)

    def test_run_and_decision_ids_are_derived_not_stored(self) -> None:
        self.assertEqual(dispatch.run_id_for("hearth-x-1"), "hearth-drain/hearth-x-1")
        self.assertEqual(dispatch.decision_id_for("hearth-x-1"), "dec_hearth-x-1")


class ArtifactStemTests(TestCase):
    def test_a_candidate_id_never_becomes_a_filename_verbatim(self) -> None:
        # ':' opens an NTFS alternate data stream; '|' and '+' are no better as
        # path components. The stem is the validated slug charset.
        raw = "backend_comparison:qwen2.5:14b:ollama-cuda+ollama-vulkan-igpu"
        stem = dispatch.artifact_stem(raw)
        self.assertNotIn(":", stem)
        self.assertNotIn("+", stem)
        self.assertRegex(stem, r"^[A-Za-z0-9._-]+$")

    def test_paths_stay_inside_the_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for builder in (dispatch.plan_artifact_path,
                            dispatch.pending_plan_artifact_path,
                            dispatch.observation_artifact_path,
                            dispatch.observed_marker_path):
                path = builder(root, "hearth-d-1", "../../escape:me")
                self.assertIn(dispatch.run_dir(root, "hearth-d-1").resolve(),
                              path.resolve().parents)


class PlanContractTests(TestCase):
    def setUp(self) -> None:
        self.schema = _schema("experiment-plan.v1.schema.json")

    def _plan(self, brief: Brief) -> dict:
        return dispatch.build_drain_experiment_plan(
            brief, "hearth-d-1", timestamp="2026-09-07T10:00:00Z",
            scope="all", backend="omen-arc")

    def test_every_source_produces_a_valid_experiment_plan(self) -> None:
        for brief in (_brief(),
                      _brief("authored", "0001-thing.md", "0001-thing", "build"),
                      _brief("refined", "refine-alpha-1", "refine-alpha-1", "build")):
            with self.subTest(source=brief.source):
                jsonschema.validate(self._plan(brief), self.schema)

    def test_only_a_candidate_plan_claims_a_candidate_provenance(self) -> None:
        self.assertEqual(self._plan(_brief())["derived_from_candidate"], "bbb_high")
        self.assertIsNone(
            self._plan(_brief("authored", "a.md", "a"))["derived_from_candidate"])

    def test_the_subject_names_no_backend_it_did_not_measure(self) -> None:
        """The drain gates on omen-arc occupancy, but the brief runs on a
        conductor builder it does not choose. Stamping the gating rung here
        would file evidence against a backend that never served the work.

        ``bbb_high`` names no combo, so all three stay null. The combo case --
        where the CANDIDATE names the subject and the plan may say so -- is the
        next test; the gating rung is still never the source."""
        subject = self._plan(_brief())["subject"]
        self.assertIsNone(subject["backend"])
        self.assertIsNone(subject["builder_id"])
        self.assertIsNone(subject["model_id"])
        self.assertEqual(subject["task_kind"], "proofing")

    def test_a_candidate_subject_is_the_combo_the_candidate_names(self) -> None:
        """B-04-R1 scope 2: the experiment-result row this plan produces is
        ABOUT that combo, so the plan says which one -- from the candidate id,
        never from the gating rung (which here is omen-arc and is not what the
        subject records)."""
        plan = self._plan(_brief(
            source_ref="known_bad_retest:omen-wsl|qwen3-30b-a3b-awq|vllm",
            slug="known_bad_retest-omen-wsl"))
        jsonschema.validate(plan, self.schema)
        self.assertEqual(plan["subject"]["builder_id"], "omen-wsl")
        self.assertEqual(plan["subject"]["model_id"], "qwen3-30b-a3b-awq")
        self.assertEqual(plan["subject"]["backend"], "vllm")
        self.assertNotEqual(plan["subject"]["backend"], "omen-arc")
        self.assertEqual(plan["subject"]["task_kind"], "proofing")
        self.assertIsNone(plan["subject"]["metric"])

    def test_an_unknown_bearing_candidate_id_leaves_the_subject_null(self) -> None:
        """Half a combo is not a combo: "unknown" is the projection's filler."""
        subject = self._plan(_brief(
            source_ref="prefer_validation:claude-frontier|gemini-3.5-flash|unknown",
            slug="prefer_validation-claude-frontier"))["subject"]
        self.assertIsNone(subject["builder_id"])
        self.assertIsNone(subject["model_id"])
        self.assertIsNone(subject["backend"])

    def test_authored_and_refined_subjects_stay_null(self) -> None:
        for brief in (_brief("authored", "0001-thing.md", "0001-thing", "build"),
                      _brief("refined", "refine-alpha-1", "refine-alpha-1", "build")):
            with self.subTest(source=brief.source):
                subject = self._plan(brief)["subject"]
                self.assertIsNone(subject["builder_id"])
                self.assertIsNone(subject["model_id"])
                self.assertIsNone(subject["backend"])

    def test_an_idle_drain_dispatch_opens_no_policy_gate(self) -> None:
        self.assertIsNone(self._plan(_brief())["gate_opened"])

    def test_the_plan_is_deterministic_for_a_given_timestamp(self) -> None:
        first = self._plan(_brief())
        second = self._plan(_brief())
        self.assertEqual(first, second)


class EventContractTests(TestCase):
    def setUp(self) -> None:
        self.event_schema = _schema("workflow-event.schema.json")
        self.brief = _brief()

    def _dispatch_event(self) -> dict:
        return dispatch.build_dispatch_event(
            self.brief, "hearth-d-1", timestamp="2026-09-07T10:00:00Z",
            plan_ref="runs/hearth-drain/hearth-d-1/artifacts/experiments/bbb_high.json",
            est_tokens=1234, scope="all", backend="omen-arc")

    def test_the_dispatch_event_validates_both_ways(self) -> None:
        event = self._dispatch_event()
        jsonschema.validate(event, self.event_schema)
        validate_event(event)  # the corpus's own validator, not just the schema

    def test_the_dispatch_event_carries_the_plan_as_an_artifact_ref(self) -> None:
        # That ref is the ONLY way project_experiments finds the plan.
        refs = self._dispatch_event()["artifact_refs"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["artifact_type"], "experiment_plan")
        self.assertEqual(refs[0]["artifact_id"], "bbb_high")

    def test_the_dispatch_event_never_claims_a_builder_assignment(self) -> None:
        """ADR-0008: the conductor is the sole scheduler. An event type of
        builder.assigned would assert the drain picked the builder."""
        event = self._dispatch_event()
        self.assertEqual(event["event_type"], "work.accepted")
        self.assertNotEqual(event["event_type"], "builder.assigned")
        self.assertNotIn("decision_class", event)
        self.assertEqual(event["actor"]["type"], "system")

    def test_every_terminal_outcome_builds_a_valid_observation_event(self) -> None:
        for outcome in sorted(dispatch.TERMINAL_OUTCOMES):
            with self.subTest(outcome=outcome):
                event = dispatch.build_observation_event(
                    "hearth-d-1", "bbb_high", timestamp="2026-09-07T11:00:00Z",
                    outcome=outcome, payload={"plan_id": "hearth-x-1"})
                jsonschema.validate(event, self.event_schema)
                validate_event(event)
                self.assertEqual(event["payload"]["experiment_id"], "bbb_high")

    def test_an_unknown_outcome_is_refused_before_it_can_land(self) -> None:
        with self.assertRaises(ValueError):
            dispatch.build_observation_event("hearth-d-1", "bbb_high",
                                             timestamp="2026-09-07T11:00:00Z",
                                             outcome="probably-fine")

    def test_the_observation_and_dispatch_event_ids_are_deterministic(self) -> None:
        # Determinism is what makes the replay guard work: a crashed tick's
        # successor recomputes the SAME event_id and can see it is already there.
        self.assertEqual(dispatch.observation_event_id("hearth-d-1"),
                         "hearth-d-1.observation")
        self.assertEqual(dispatch.dispatch_event_id("hearth-d-1"),
                         "hearth-d-1.dispatch")

    def test_a_bad_event_never_reaches_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            with self.assertRaises(ValidationError):
                dispatch.append_corpus_event(path, {"event_type": "work.accepted"})
            self.assertFalse(path.exists())


class CapacityObservationTests(TestCase):
    def setUp(self) -> None:
        self.schema = _schema("capacity-observation.v1.schema.json")

    def test_a_named_builder_produces_a_valid_observation(self) -> None:
        for succeeded in (True, False):
            with self.subTest(succeeded=succeeded):
                observation = dispatch.build_capacity_observation(
                    "hearth-d-1", "bbb_high", timestamp="2026-09-07T11:00:00Z",
                    builder_id="cc-builder-2", model_id="qwen3-30b-a3b",
                    backend="omen-arc", succeeded=succeeded,
                    task_kind="proofing", est_tokens=900)
                jsonschema.validate(observation, self.schema)
                self.assertEqual(observation["outcome"],
                                 "success" if succeeded else "error")
                self.assertEqual(observation["decision_id"], "dec_hearth-d-1")
                # B-04-R1: the whole combo is named. Leaving model/backend null
                # is what put <builder>|unknown|unknown into the projection.
                self.assertEqual(observation["model_id"], "qwen3-30b-a3b")
                self.assertEqual(observation["backend"], "omen-arc")

    def test_an_unnamed_builder_is_refused_rather_than_filled_with_unknown(self) -> None:
        """capacity-observation.v1 requires a builder_id. Writing "unknown"
        would file real evidence against a combo nobody ran, so a run with no
        winner gets the observation EVENT and no capacity artifact."""
        for builder in (None, "", "   "):
            with self.subTest(builder=builder):
                with self.assertRaises(ValueError):
                    dispatch.build_capacity_observation(
                        "hearth-d-1", "bbb_high", timestamp="2026-09-07T11:00:00Z",
                        builder_id=builder, model_id="qwen3-30b-a3b",
                        backend="omen-arc", succeeded=True)

    def test_an_unnamed_model_or_backend_is_refused_too(self) -> None:
        """THE B-04-R1 GUARD. project_capacity maps a null model_id/backend to
        the combo key "unknown" (_combo_key L35-41, reduce_capacity L100-102),
        so an artifact written with either missing files real evidence against
        a combo that names no machine. It must be impossible to build one."""
        for field in ("model_id", "backend"):
            for value in (None, "", "   ", 7):
                with self.subTest(field=field, value=value):
                    kwargs = {"builder_id": "cc-builder-2",
                              "model_id": "qwen3-30b-a3b", "backend": "omen-arc"}
                    kwargs[field] = value
                    with self.assertRaises(ValueError):
                        dispatch.build_capacity_observation(
                            "hearth-d-1", "bbb_high",
                            timestamp="2026-09-07T11:00:00Z", succeeded=True,
                            **kwargs)

    def test_the_literal_unknown_is_refused_in_every_identity_field(self) -> None:
        """"unknown" is the PROJECTION's placeholder for a field nobody knows.
        Passing it through would reach the same bad bucket by the front door."""
        for field in ("builder_id", "model_id", "backend"):
            with self.subTest(field=field):
                kwargs = {"builder_id": "cc-builder-2",
                          "model_id": "qwen3-30b-a3b", "backend": "omen-arc"}
                kwargs[field] = dispatch.UNKNOWN_COMBO_SEGMENT
                with self.assertRaises(ValueError):
                    dispatch.build_capacity_observation(
                        "hearth-d-1", "bbb_high",
                        timestamp="2026-09-07T11:00:00Z", succeeded=True,
                        **kwargs)

    def test_model_and_backend_cannot_be_forgotten_by_a_call_site(self) -> None:
        # Required keyword-only arguments: omitting them is a TypeError at the
        # call, not a null that survives into the corpus.
        with self.assertRaises(TypeError):
            dispatch.build_capacity_observation(
                "hearth-d-1", "bbb_high", timestamp="2026-09-07T11:00:00Z",
                builder_id="cc-builder-2", succeeded=True)


class ComboDerivationTests(TestCase):
    """The combo rule, checked against the id shapes the corpus really makes.

    Every expectation here is traceable to a literal in
    ``tools/workflow/project_experiments.py`` or ``project_coverage.py``; the
    real-id cases are copied out of ``knowledge/experiment_candidates.json``.
    """

    def test_the_four_combo_types_carry_the_combo_after_the_type(self) -> None:
        for kind in sorted(dispatch.COMBO_EXPERIMENT_TYPES):
            with self.subTest(kind=kind):
                combo = dispatch.combo_from_candidate_id(
                    f"{kind}:omen|qwen3-30b-a3b|omen-arc")
                self.assertEqual(combo, ("omen", "qwen3-30b-a3b", "omen-arc"))
                self.assertEqual(combo.builder_id, "omen")
                self.assertEqual(combo.model_id, "qwen3-30b-a3b")
                self.assertEqual(combo.backend, "omen-arc")

    def test_a_model_id_containing_colons_or_slashes_survives(self) -> None:
        """The id is split on the FIRST colon only. A last-colon split, or a
        3-way split on ':', would mangle every ollama-style model id and every
        .gguf path -- both of which are in the live candidate table."""
        self.assertEqual(
            dispatch.combo_from_candidate_id(
                "prefer_validation:omen-5070|qwen2.5:14b|ollama-cuda"),
            ("omen-5070", "qwen2.5:14b", "ollama-cuda"))
        gguf = "/home/derek/baseline/models/Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"
        self.assertEqual(
            dispatch.combo_from_candidate_id(
                f"prefer_validation:claude-frontier|{gguf}|am4-oxen"),
            ("claude-frontier", gguf, "am4-oxen"))

    def test_the_two_combo_shaped_coverage_gaps_are_decoded(self) -> None:
        for gap in sorted(dispatch.COMBO_COVERAGE_GAP_TYPES):
            with self.subTest(gap=gap):
                self.assertEqual(
                    dispatch.combo_from_candidate_id(
                        f"coverage_probe:{gap}:am4|gpt-oss-120b|am4-moe"),
                    ("am4", "gpt-oss-120b", "am4-moe"))

    def test_a_single_workflow_evidence_gap_is_not_a_combo(self) -> None:
        """It joins "name=value" invariant pairs with '|' too, so shape alone
        cannot tell them apart -- the gap type is what does."""
        for candidate_id in (
            "coverage_probe:single_workflow_evidence:task_backend:"
            "task_kind=local_generate|backend=omen-arc",
            "coverage_probe:single_workflow_evidence:model_portability:"
            "model_id=qwen2.5:7b-instruct",
            "coverage_probe:single_workflow_evidence:x:a=1|b=2|c=3",
        ):
            with self.subTest(candidate_id=candidate_id):
                self.assertIsNone(dispatch.combo_from_candidate_id(candidate_id))

    def test_the_non_combo_experiment_types_name_no_combo(self) -> None:
        for candidate_id in (
            "backend_comparison:gemini-3.5-flash:gcp-gemini+unknown",
            "backend_comparison:qwen2.5:14b:ollama-cuda+ollama-vulkan",
            "prediction_bias_calibration:qwen3-30b-a3b:tokens_per_s",
            "qualification_run:cap_local_generate_omen_arc",
            "confidence_calibration:corpus",
            "coverage_probe:stale_capability:cap_x",
        ):
            with self.subTest(candidate_id=candidate_id):
                self.assertIsNone(dispatch.combo_from_candidate_id(candidate_id))

    def test_an_unknown_segment_voids_the_whole_combo(self) -> None:
        """THE CASE THIS REPAIR EXISTS FOR. _combo writes "unknown" for a field
        the corpus does not know, and ids like this are in the live candidate
        table right now. Half a combo is not a combo."""
        for candidate_id in (
            "prefer_validation:claude-frontier|gemini-3.5-flash|unknown",
            "prefer_validation:unknown|gemini-3.5-flash|gcp-gemini",
            "known_bad_retest:a|unknown|b",
            "coverage_probe:unmeasured_metrics:a|b|unknown",
        ):
            with self.subTest(candidate_id=candidate_id):
                self.assertIsNone(dispatch.combo_from_candidate_id(candidate_id))

    def test_malformed_and_non_string_ids_are_refused_not_guessed(self) -> None:
        for candidate_id in (None, 7, "", "bbb_high", "prefer_validation:",
                             "prefer_validation:a|b", "prefer_validation:a|b|c|d",
                             "prefer_validation:a||c", "prefer_validation:a| |c",
                             "coverage_probe:unobserved_combo"):
            with self.subTest(candidate_id=candidate_id):
                self.assertIsNone(dispatch.combo_from_candidate_id(candidate_id))

    def test_only_a_candidate_brief_has_a_combo(self) -> None:
        combo_id = "prefer_validation:omen|qwen3-30b-a3b|omen-arc"
        self.assertEqual(
            dispatch.combo_for(_brief(source_ref=combo_id, slug="c")),
            ("omen", "qwen3-30b-a3b", "omen-arc"))
        # An authored/refined source_ref is a filename or an intent id; even if
        # it happened to look combo-shaped it is not a combo experiment.
        self.assertIsNone(dispatch.combo_for(_brief("authored", combo_id, "c")))
        self.assertIsNone(dispatch.combo_for(_brief("refined", combo_id, "c")))

    def test_the_combo_is_recovered_from_the_slot_not_stored_on_it(self) -> None:
        """Scope item 5: the record already carries source/source_ref, so no key
        is added and the in-flight contract stays at v1."""
        brief = _brief(source_ref="prefer_validation:omen|qwen3-30b-a3b|omen-arc",
                       slug="c")
        record = dispatch.new_in_flight(
            brief, "hearth-d-1", dispatched_at="2026-09-07T10:00:00Z",
            plan_artifact="p", dispatch_event_id="hearth-d-1.dispatch",
            corpus_root="C:/tmp")
        self.assertEqual(record["contract_version"], "bankedfire-drain-inflight.v1")
        self.assertNotIn("combo", record)
        self.assertEqual(dispatch.combo_from_in_flight(record),
                         ("omen", "qwen3-30b-a3b", "omen-arc"))
        # A legacy / hand-edited / non-dict slot answers None, never raises.
        for record in (None, "legacy-plan-id", {}, {"source": "candidate"},
                       {"source": "authored", "source_ref": "a.md"}):
            with self.subTest(record=record):
                self.assertIsNone(dispatch.combo_from_in_flight(record))


class CapacityWriteDecisionTests(TestCase):
    """Write the artifact only when the run is evidence about THIS combo."""

    COMBO = dispatch.Combo("cc-builder-2", "qwen3-30b-a3b", "omen-arc")

    def test_a_matching_winner_writes_the_combo(self) -> None:
        combo, reason = dispatch.capacity_write_decision(self.COMBO, "cc-builder-2")
        self.assertEqual(combo, self.COMBO)
        self.assertIsNone(reason)

    def test_no_combo_skips_with_no_combo(self) -> None:
        for winner in ("cc-builder-2", None):
            with self.subTest(winner=winner):
                combo, reason = dispatch.capacity_write_decision(None, winner)
                self.assertIsNone(combo)
                self.assertEqual(reason, dispatch.CAPACITY_SKIP_NO_COMBO)

    def test_a_different_or_absent_winner_skips_with_winner_mismatch(self) -> None:
        for winner in ("cc-builder-3", None, "", "  ", 7, "CC-BUILDER-2"):
            with self.subTest(winner=winner):
                combo, reason = dispatch.capacity_write_decision(self.COMBO, winner)
                self.assertIsNone(combo)
                self.assertEqual(reason, dispatch.CAPACITY_SKIP_WINNER_MISMATCH)

    def test_exactly_one_of_the_two_is_ever_returned(self) -> None:
        for combo in (None, self.COMBO):
            for winner in (None, "cc-builder-2", "cc-builder-3"):
                with self.subTest(combo=combo, winner=winner):
                    got, reason = dispatch.capacity_write_decision(combo, winner)
                    self.assertEqual(got is None, reason is not None)


class PendingPlanTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.plan = dispatch.build_drain_experiment_plan(
            _brief(), "hearth-d-1", timestamp="2026-09-07T10:00:00Z")

    def test_a_pending_plan_is_not_at_the_referenced_path(self) -> None:
        dispatch.write_pending_plan(self.tmp, "hearth-d-1", self.plan)
        self.assertTrue(dispatch.pending_plan_artifact_path(
            self.tmp, "hearth-d-1", "bbb_high").is_file())
        self.assertFalse(dispatch.plan_artifact_path(
            self.tmp, "hearth-d-1", "bbb_high").is_file())

    def test_publishing_moves_it_and_is_idempotent(self) -> None:
        dispatch.write_pending_plan(self.tmp, "hearth-d-1", self.plan)
        self.assertTrue(dispatch.publish_plan(self.tmp, "hearth-d-1", "bbb_high"))
        final = dispatch.plan_artifact_path(self.tmp, "hearth-d-1", "bbb_high")
        self.assertTrue(final.is_file())
        self.assertEqual(json.loads(final.read_text(encoding="utf-8")), self.plan)
        # Second call: nothing pending, nothing changed, no raise.
        self.assertFalse(dispatch.publish_plan(self.tmp, "hearth-d-1", "bbb_high"))
        self.assertTrue(final.is_file())

    def test_publishing_nothing_is_not_an_error(self) -> None:
        self.assertFalse(dispatch.publish_plan(self.tmp, "hearth-d-1", "bbb_high"))


class AtomicWriteTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_a_crash_between_write_and_swap_leaves_the_old_file_intact(self) -> None:
        path = self.tmp / "state.json"
        path.write_text("original\n", encoding="utf-8")

        def boom(src, dst):
            raise OSError("power cut between the temp write and the swap")

        with self.assertRaises(OSError):
            dispatch.write_text_atomic(path, "replacement\n", replace_fn=boom)
        self.assertEqual(path.read_text(encoding="utf-8"), "original\n")
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["state.json"],
                         "the temp file must not be left behind")


class ReplayGuardTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.tmp / "events.jsonl"

    def test_a_missing_file_has_recorded_nothing(self) -> None:
        self.assertFalse(dispatch.event_already_recorded(self.path, "x.observation"))

    def test_an_appended_event_is_found_by_id(self) -> None:
        event = dispatch.build_observation_event(
            "hearth-d-1", "bbb_high", timestamp="2026-09-07T11:00:00Z",
            outcome=dispatch.OUTCOME_SUCCEEDED)
        dispatch.append_corpus_event(self.path, event)
        self.assertTrue(dispatch.event_already_recorded(self.path, "hearth-d-1.observation"))
        self.assertFalse(dispatch.event_already_recorded(self.path, "hearth-d-2.observation"))
        self.assertEqual(validate_file(self.path), [])

    def test_a_torn_line_does_not_stop_the_scan(self) -> None:
        """A half-written line must not make the guard blind -- being blind here
        means appending the observation a second time."""
        event = dispatch.build_observation_event(
            "hearth-d-1", "bbb_high", timestamp="2026-09-07T11:00:00Z",
            outcome=dispatch.OUTCOME_SUCCEEDED)
        dispatch.append_corpus_event(self.path, event)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write('{"event_id": "torn"')
        self.assertTrue(dispatch.event_already_recorded(self.path, "hearth-d-1.observation"))


class InFlightPhaseTests(TestCase):
    def _record(self, **overrides) -> dict:
        record = dispatch.new_in_flight(
            _brief(), "hearth-d-1", dispatched_at="2026-09-07T10:00:00Z",
            plan_artifact="runs/hearth-drain/hearth-d-1/artifacts/experiments/bbb_high.json",
            dispatch_event_id="hearth-d-1.dispatch", corpus_root="C:/tmp",
            est_tokens=900)
        record.update(overrides)
        return record

    def test_no_slot_has_no_phase(self) -> None:
        self.assertIsNone(dispatch.in_flight_phase(None))
        self.assertIsNone(dispatch.in_flight_phase("not a record"))

    def test_a_fresh_slot_is_prepared(self) -> None:
        record = self._record()
        self.assertEqual(dispatch.in_flight_phase(record), dispatch.PHASE_PREPARED)
        self.assertFalse(record["submitted"])
        self.assertIsNone(record["plan_id"])
        self.assertEqual(record["experiment_id"], "bbb_high")
        self.assertEqual(record["run_id"], "hearth-drain/hearth-d-1")

    def test_the_attempt_flag_is_what_makes_the_outcome_unknown(self) -> None:
        """prepared vs attempted is the whole reconcile rule: before the submit
        call is entered nothing can be in the inbox, after it the answer is
        genuinely unknown and the lane must fail closed."""
        self.assertEqual(dispatch.in_flight_phase(self._record(submit_attempted=True)),
                         dispatch.PHASE_ATTEMPTED)

    def test_a_plan_id_means_submitted_whatever_else_the_record_says(self) -> None:
        self.assertEqual(
            dispatch.in_flight_phase(self._record(submit_attempted=True,
                                                  plan_id="hearth-x-1")),
            dispatch.PHASE_SUBMITTED)

    def test_a_record_with_no_attempt_key_is_never_read_as_unknown(self) -> None:
        # Inventing an "unknown" for a hand-written or older slot would wedge a
        # lane that is not actually ambiguous.
        self.assertEqual(dispatch.in_flight_phase({"dispatch_id": "d"}),
                         dispatch.PHASE_PREPARED)
        self.assertEqual(dispatch.in_flight_phase({"plan_id": "hearth-x-1"}),
                         dispatch.PHASE_SUBMITTED)


class ResultReadingTests(TestCase):
    def test_the_full_task_status_shape_is_read(self) -> None:
        status = {"ok": True, "done": True, "result_path": "runs/x/result.json",
                  "result": {"ok": True, "winner": "cc-builder-2"}}
        self.assertEqual(dispatch.read_result(status),
                         (True, "cc-builder-2", "runs/x/result.json"))

    def test_the_out_file_ack_shape_is_read_too(self) -> None:
        # task_status(out_file=...) lifts these to the top level instead.
        status = {"ok": True, "done": True, "result_path": "runs/x/result.json",
                  "result_ok": False, "winner": "cc-builder-3"}
        self.assertEqual(dispatch.read_result(status),
                         (False, "cc-builder-3", "runs/x/result.json"))

    def test_a_winner_that_is_not_a_string_is_absent_not_coerced(self) -> None:
        for winner in (None, "", "  ", 7, {"id": "x"}):
            with self.subTest(winner=winner):
                _ok, got, _path = dispatch.read_result({"result": {"ok": True,
                                                                   "winner": winner}})
                self.assertIsNone(got)

    def test_outcome_mapping(self) -> None:
        self.assertEqual(dispatch.completion_outcome(True, "cc-builder-2"),
                         dispatch.OUTCOME_SUCCEEDED)
        self.assertEqual(dispatch.completion_outcome(False, "cc-builder-2"),
                         dispatch.OUTCOME_FAILED)
        self.assertEqual(dispatch.completion_outcome(True, None),
                         dispatch.OUTCOME_NO_WINNER)
        # A run that named a builder but never said it worked is not a success.
        self.assertEqual(dispatch.completion_outcome(None, "cc-builder-2"),
                         dispatch.OUTCOME_FAILED)


class NoAccidentalStateTests(TestCase):
    """Same rule the rest of hearth.backlog obeys: naming a path must never
    create it. A path builder that quietly mkdir'd would put an empty run dir in
    the live corpus every time a tick merely computed where it WOULD write."""

    def test_naming_paths_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dispatch.run_dir(root, "hearth-d-1")
            dispatch.events_path(root, "hearth-d-1")
            dispatch.plan_artifact_path(root, "hearth-d-1", "bbb_high")
            dispatch.pending_plan_artifact_path(root, "hearth-d-1", "bbb_high")
            dispatch.observation_artifact_path(root, "hearth-d-1", "bbb_high")
            dispatch.observed_marker_path(root, "hearth-d-1", "bbb_high")
            self.assertEqual(list(root.iterdir()), [])

    def test_building_documents_creates_nothing(self) -> None:
        brief = _brief()
        dispatch.build_drain_experiment_plan(brief, "hearth-d-1",
                                             timestamp="2026-09-07T10:00:00Z")
        dispatch.build_dispatch_event(brief, "hearth-d-1",
                                      timestamp="2026-09-07T10:00:00Z", plan_ref="p")
        dispatch.build_observation_event("hearth-d-1", "bbb_high",
                                        timestamp="2026-09-07T11:00:00Z",
                                        outcome=dispatch.OUTCOME_SUCCEEDED)
        self.assertFalse((_REPO_ROOT / "runs" / dispatch.RUN_NAMESPACE).exists(),
                         "the live drain corpus root must not exist yet")
