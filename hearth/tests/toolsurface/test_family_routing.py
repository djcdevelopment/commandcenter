"""C-05 — task-family-aware routing through the door.

The authored family evidence (hearth/etc/routing-families.toml, R10/ADR-0039)
had one consumer and never reached a dispatch. These tests pin the contract that
lets it steer a route WITHOUT a second scheduler and WITHOUT hidden substitution:

  precedence: endpoint pin > backend pin > explicit quality/task > task_family > default

and the routed_by grammar that makes every family route visible on the ledger:

  family:<name>:pinned:<rung>          the recommended rung is pin-only
  family:<name>:<inner>                tag/default route, inner reason preserved
  family:<name>:escalation:<a>-><b>    a family route that climbed one rung

Everything here is hermetic: a fixture pool, a patched ``_post``/``urlopen``,
and a forced-available occupancy reading. No rung is contacted, no network is
touched, and nothing is written outside a TemporaryDirectory.
"""
from __future__ import annotations

import json
import os
import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.scheduler.families import (ASSAY_FAMILIES, FAMILY_TAGS, load_families,
                                       tags_for)
from hearth.toolsurface import inference
from hearth.toolsurface.backends import load_pool
from hearth.toolsurface.inference import local_generate

# A pool shaped like the real one, with the model ids the REAL routing-families
# declaration names, so `recommend()` resolves a genuine backend_hint against it:
#   omen-arc      qwen3-30b-a3b, tagged  -> opportunistically routable
#   omen-arc-27b  qwen38-27b,    UNTAGGED -> pin-only (pin_required)
#   cloud         gemini-3.5-flash, tagged cloud-overflow
# Ollama api throughout so no auth env is needed; `_post` is patched.
_POOL = textwrap.dedent("""
    default = "omen-arc"

    [[backend]]
    name = "omen-arc"
    endpoint = "http://127.0.0.1:8082"
    api = "ollama"
    models = ["qwen3-30b-a3b"]
    tags = ["default", "code", "reasoning", "big-context"]
    [backend.settings]
    context_bytes = 229376

    [[backend]]
    name = "omen-arc-27b"
    endpoint = "http://127.0.0.1:8084"
    api = "ollama"
    models = ["qwen38-27b"]
    tags = []
    [backend.settings]
    context_bytes = 20480

    [[backend]]
    name = "cloud"
    endpoint = "http://cloud"
    api = "ollama"
    models = ["gemini-3.5-flash"]
    tags = ["cloud-overflow"]
""")

# quote_retrieval's authored floor is 4096 prompt tokens and the door estimates
# tokens as bytes//4, so these are derived, not magic: a re-authored floor moves
# them instead of silently turning a test into a no-op.
_FLOOR_TOKENS = 4096
_DEEP_BYTES = 20000        # >= floor, <= omen-arc-27b's 20480 B budget
_SHALLOW_BYTES = 4000      # < floor
_OVER_BUDGET_BYTES = 30000  # >= floor, > omen-arc-27b's budget


class _HermeticDoor(TestCase):
    """Fixture pool + forced-available occupancy + no env escape hatches."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pool_path = self.tmp / "backends.toml"
        pool_path.write_text(_POOL, encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {"HEARTH_BACKENDS": str(pool_path)}))
        # HEARTH_OLLAMA would win over a family TAG route (an operator env
        # override is as deliberate as a pin); clear it so these tests measure
        # the family, not the shell.
        os.environ.pop("HEARTH_OLLAMA", None)
        os.environ.pop("HEARTH_ROUTING_FAMILIES", None)
        self.enterContext(patch("hearth.toolsurface.inference.check_occupancy",
                                return_value={"occupancy": "available"}))
        self.post = self.enterContext(patch("hearth.toolsurface.inference._post"))
        # No "model" key in the body, so the result's model is the RESOLVED model
        # -- which is what the no-hidden-substitution assertions read.
        self.post.return_value = ({"response": "ok"}, None)

    def prompt(self, n_bytes: int) -> str:
        return "x" * n_bytes

    def assertFloorsStillDerived(self) -> None:
        pref = load_families().get("quote_retrieval")
        self.assertEqual(pref.min_prompt_tokens, _FLOOR_TOKENS)
        self.assertLess(_SHALLOW_BYTES // 4, _FLOOR_TOKENS)
        self.assertGreaterEqual(_DEEP_BYTES // 4, _FLOOR_TOKENS)
        budget = load_pool().by_name("omen-arc-27b").context_bytes()
        self.assertLessEqual(_DEEP_BYTES, budget)
        self.assertGreater(_OVER_BUDGET_BYTES, budget)


class FamilyTagTableTests(TestCase):
    """FAMILY_TAGS is data with two obligations: cover every declared family,
    and name only tags a live rung actually carries."""

    def setUp(self) -> None:
        # The REAL declarations: this class is the binding between the tag table,
        # routing-families.toml and backends.toml.
        self.enterContext(patch.dict(os.environ, {}, clear=False))
        os.environ.pop("HEARTH_BACKENDS", None)
        os.environ.pop("HEARTH_ROUTING_FAMILIES", None)

    def test_covers_every_declared_family_and_every_assay_family(self) -> None:
        declared = set(load_families().names())
        self.assertEqual(declared, set(FAMILY_TAGS),
                         "FAMILY_TAGS and routing-families.toml must name the same families")
        for family in ASSAY_FAMILIES:
            self.assertIn(family, FAMILY_TAGS, family)
        self.assertIn("default", FAMILY_TAGS)

    def test_every_declared_tag_maps_to_a_live_configured_rung(self) -> None:
        """A tag no rung carries is a routing wish, not a route."""
        pool = load_pool()
        live = {tag for rung in pool.backends if not rung.retired for tag in rung.tags}
        for family, tags in sorted(FAMILY_TAGS.items()):
            self.assertTrue(tags, f"{family}: declares no routing tag")
            for tag in tags:
                self.assertIn(tag, live,
                              f"{family}: tag {tag!r} is on no live rung in backends.toml")

    def test_tags_for_falls_to_default_and_returns_a_copy(self) -> None:
        self.assertEqual(tags_for("reasoning_planning"), ["reasoning"])
        self.assertEqual(tags_for("banana_peeling"), FAMILY_TAGS["default"])
        self.assertEqual(tags_for(None), FAMILY_TAGS["default"])
        borrowed = tags_for("reasoning_planning")
        borrowed.append("tampered")
        self.assertEqual(FAMILY_TAGS["reasoning_planning"], ["reasoning"])
        self.assertEqual(tags_for("reasoning_planning"), ["reasoning"])


class AbsentTaskFamilyIsUnchangedTests(_HermeticDoor):
    """The regression guard: a caller who never passes task_family sees exactly
    today's routing, and both stamps read None."""

    def _triple(self, result: dict) -> tuple:
        return result["routed_by"], result["backend"], result["model"]

    def test_default_route_unchanged(self) -> None:
        without = local_generate("q")
        explicit_none = local_generate("q", task_family=None)
        self.assertEqual(self._triple(without), ("default", "omen-arc", "qwen3-30b-a3b"))
        self.assertEqual(self._triple(without), self._triple(explicit_none))
        self.assertIsNone(without["task_family"])
        self.assertIsNone(without["family_recommendation"])

    def test_quality_good_route_unchanged(self) -> None:
        result = local_generate("q", quality="good")
        self.assertEqual(self._triple(result),
                         ("quality-good:tag:cloud-overflow", "cloud", "gemini-3.5-flash"))
        self.assertIsNone(result["task_family"])
        self.assertIsNone(result["family_recommendation"])

    def test_pinned_route_unchanged(self) -> None:
        result = local_generate("q", backend="omen-arc-27b")
        self.assertEqual(self._triple(result),
                         ("pinned:omen-arc-27b", "omen-arc-27b", "qwen38-27b"))
        self.assertIsNone(result["task_family"])
        self.assertIsNone(result["family_recommendation"])

    def test_escalation_route_unchanged(self) -> None:
        self.post.side_effect = [(None, "connection refused"), ({"response": "ok"}, None)]
        result = local_generate("q", task="reasoning")
        self.assertEqual(self._triple(result),
                         ("escalation:omen-arc->cloud", "cloud", "gemini-3.5-flash"))
        self.assertEqual(self.post.call_count, 2)
        self.assertIsNone(result["task_family"])
        self.assertIsNone(result["family_recommendation"])

    def test_ask_path_unchanged_and_stamped_none(self) -> None:
        result = local_generate("q", quality="best")
        self.assertEqual(result["routed_by"], "ask:quality-best")
        self.assertIsNone(result["task_family"])
        self.assertIsNone(result["family_recommendation"])
        self.post.assert_not_called()


class CallerPinBeatsTaskFamilyTests(_HermeticDoor):
    """Precedence rung 1 and 2: either pin outranks authored evidence, and the
    evidence still rides back so the caller can see the advice they overrode."""

    def test_backend_pin_wins_and_family_is_advisory_only(self) -> None:
        self.assertFloorsStillDerived()
        result = local_generate(self.prompt(_DEEP_BYTES), backend="cloud",
                                task_family="quote_retrieval")
        self.assertEqual(result["routed_by"], "pinned:cloud")
        self.assertEqual(result["backend"], "cloud")
        self.assertEqual(result["model"], "gemini-3.5-flash")
        self.assertEqual(result["task_family"], "quote_retrieval")
        # The advice that was NOT taken: a deep quote_retrieval prompt wants the
        # pin-only 27B rung. Overridden, but recorded.
        self.assertEqual(result["family_recommendation"]["model_id"], "qwen38-27b")
        self.assertEqual(result["family_recommendation"]["backend_hint"], "omen-arc-27b")
        self.assertTrue(result["family_recommendation"]["pin_required"])

    def test_endpoint_pin_wins(self) -> None:
        result = local_generate(self.prompt(_DEEP_BYTES), endpoint="http://127.0.0.1:9999",
                                task_family="quote_retrieval")
        self.assertEqual(result["routed_by"], "pinned-endpoint")
        self.assertEqual(result["task_family"], "quote_retrieval")
        self.assertEqual(result["family_recommendation"]["model_id"], "qwen38-27b")

    def test_explicit_quality_wins_over_family(self) -> None:
        result = local_generate(self.prompt(_DEEP_BYTES), quality="good",
                                task_family="quote_retrieval")
        self.assertEqual(result["routed_by"], "quality-good:tag:cloud-overflow")
        self.assertEqual(result["backend"], "cloud")
        self.assertEqual(result["task_family"], "quote_retrieval")
        self.assertIsNotNone(result["family_recommendation"])

    def test_explicit_task_tag_wins_over_family(self) -> None:
        result = local_generate("q", task="code", task_family="document_ocr")
        self.assertEqual(result["routed_by"], "tag:code")
        self.assertEqual(result["backend"], "omen-arc")
        self.assertEqual(result["task_family"], "document_ocr")
        self.assertEqual(result["family_recommendation"]["model_id"], "gemini-3.5-flash")


class FamilyTagRouteTests(_HermeticDoor):
    """Precedence rung 4: the family routes by TAG when its recommended model is
    opportunistically reachable."""

    def test_reasoning_planning_routes_by_the_reasoning_tag(self) -> None:
        original = inference._resolve_target
        with patch.object(inference, "_resolve_target", side_effect=original) as spy:
            result = local_generate("q", task_family="reasoning_planning")
        self.assertEqual(spy.call_args.kwargs["tags"], ["reasoning"])
        self.assertTrue(result["routed_by"].startswith("family:reasoning_planning:"))
        self.assertEqual(result["routed_by"], "family:reasoning_planning:tag:reasoning")
        self.assertEqual(result["backend"], "omen-arc")

    def test_vision_family_routes_to_the_cloud_overflow_rung(self) -> None:
        result = local_generate("q", task_family="document_ocr")
        self.assertEqual(result["routed_by"], "family:document_ocr:tag:cloud-overflow")
        self.assertEqual(result["backend"], "cloud")

    def test_unknown_family_routes_deterministically_through_default(self) -> None:
        first = local_generate("q", task_family="banana_peeling")
        second = local_generate("q", task_family="banana_peeling")
        self.assertEqual(first["routed_by"], "family:default:tag:default")
        self.assertEqual(first["routed_by"], second["routed_by"])
        self.assertEqual(first["task_family"], "banana_peeling")
        self.assertEqual(first["family_recommendation"]["family"], "default")
        self.assertEqual(first["family_recommendation"]["requested_family"], "banana_peeling")

    def test_quote_retrieval_below_the_floor_takes_the_default_model_route(self) -> None:
        self.assertFloorsStillDerived()
        result = local_generate(self.prompt(_SHALLOW_BYTES), task_family="quote_retrieval")
        self.assertEqual(result["routed_by"], "family:quote_retrieval:tag:big-context")
        self.assertEqual(result["backend"], "omen-arc")
        self.assertEqual(result["model"], "qwen3-30b-a3b")
        self.assertFalse(result["family_recommendation"]["pin_required"])

    def test_bad_task_family_type_is_rejected_before_anything_happens(self) -> None:
        for bad in ("", "   ", 7):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    local_generate("q", task_family=bad)
        self.post.assert_not_called()


class FamilyPinRouteTests(_HermeticDoor):
    """Precedence rung 4, pin-only branch: the recommended rung carries no tags,
    so the door names it explicitly -- and it is a pin in every respect."""

    def test_deep_quote_retrieval_pins_the_untagged_27b_rung(self) -> None:
        self.assertFloorsStillDerived()
        result = local_generate(self.prompt(_DEEP_BYTES), task_family="quote_retrieval")
        self.assertEqual(result["routed_by"], "family:quote_retrieval:pinned:omen-arc-27b")
        self.assertEqual(result["backend"], "omen-arc-27b")
        self.assertEqual(result["model"], "qwen38-27b")
        self.assertTrue(result["family_recommendation"]["pin_required"])
        self.post.assert_called_once()

    def test_over_budget_family_pin_is_refused_not_silently_rerouted(self) -> None:
        """ADR-0031: a pin picks the rung, not the physics. The refusal carries
        the recommendation so the caller sees exactly why."""
        self.assertFloorsStillDerived()
        result = local_generate(self.prompt(_OVER_BUDGET_BYTES), task_family="quote_retrieval")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "routing_refusal")
        refusal = result["routing_refusal"]
        self.assertEqual(refusal["reason"], "payload_over_budget_for_pinned_backend")
        self.assertEqual(refusal["attempted"][0]["name"], "omen-arc-27b")
        self.assertTrue(refusal["attempted"][0]["pinned"])
        self.assertEqual(result["task_family"], "quote_retrieval")
        self.assertEqual(result["family_recommendation"]["backend_hint"], "omen-arc-27b")
        self.post.assert_not_called()  # no dispatch, and no quiet fall back to omen-arc


class FamilyEscalationTests(_HermeticDoor):
    """A2 still holds: one climb for a tag route, none for a pin. No loop."""

    def test_family_tag_route_escalates_exactly_once(self) -> None:
        self.post.side_effect = [(None, "connection refused"), ({"response": "ok"}, None)]
        result = local_generate("q", task_family="reasoning_planning")
        self.assertTrue(result["ok"])
        self.assertEqual(result["routed_by"],
                         "family:reasoning_planning:escalation:omen-arc->cloud")
        self.assertEqual(result["backend"], "cloud")
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(result["task_family"], "reasoning_planning")
        self.assertIsNotNone(result["family_recommendation"])

    def test_family_pin_never_escalates(self) -> None:
        self.assertFloorsStillDerived()
        self.post.side_effect = [(None, "connection refused"), ({"response": "ok"}, None)]
        result = local_generate(self.prompt(_DEEP_BYTES), task_family="quote_retrieval")
        self.assertFalse(result["ok"])
        self.assertEqual(result["routed_by"], "family:quote_retrieval:pinned:omen-arc-27b")
        self.assertEqual(self.post.call_count, 1)
        self.assertNotIn("escalation", result)


class NoHiddenSubstitutionTests(_HermeticDoor):
    """Whenever the dispatched model differs from the caller's `model` argument,
    the stamped result must EXPLAIN the difference -- either the family's
    recommended model_id, or the chosen backend's own declared default."""

    def _assert_explained(self, result: dict, caller_model) -> None:
        dispatched = result["model"]
        if dispatched == caller_model:
            return
        recommendation = result.get("family_recommendation") or {}
        backend = load_pool().by_name(result["backend"])
        backend_default = backend.models[0] if backend and backend.models else None
        self.assertIn(dispatched, {recommendation.get("model_id"), backend_default},
                      f"unexplained model substitution: {caller_model!r} -> {dispatched!r} "
                      f"(routed_by {result['routed_by']})")

    def test_every_family_route_explains_its_model(self) -> None:
        self.assertFloorsStillDerived()
        cases = [
            ("family pin", self.prompt(_DEEP_BYTES), {"task_family": "quote_retrieval"}),
            ("family tag", "q", {"task_family": "reasoning_planning"}),
            ("family vision", "q", {"task_family": "document_ocr"}),
            ("family default", "q", {"task_family": "summarization"}),
            ("caller pin", "q", {"backend": "cloud", "task_family": "quote_retrieval"}),
            ("no family", "q", {}),
        ]
        for label, prompt, kwargs in cases:
            with self.subTest(case=label):
                result = local_generate(prompt, **kwargs)
                self._assert_explained(result, kwargs.get("model"))

    def test_family_pin_model_is_the_recommended_one(self) -> None:
        result = local_generate(self.prompt(_DEEP_BYTES), task_family="quote_retrieval")
        self.assertEqual(result["model"], result["family_recommendation"]["model_id"])

    def test_family_tag_model_is_the_backend_default(self) -> None:
        result = local_generate("q", task_family="reasoning_planning")
        chosen = load_pool().by_name(result["backend"])
        self.assertEqual(result["model"], chosen.models[0])


class FamilyConfigFailureTests(_HermeticDoor):
    """Loud fallbacks: unreadable authored evidence refuses the call rather than
    routing on a default nobody authored -- and only for callers who asked."""

    def setUp(self) -> None:
        super().setUp()
        self.missing = self.tmp / "nowhere" / "routing-families.toml"
        self.enterContext(patch.dict(os.environ,
                                     {"HEARTH_ROUTING_FAMILIES": str(self.missing)}))

    def test_missing_families_file_refuses_the_family_call(self) -> None:
        result = local_generate("q", task_family="summarization")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "family_config_error")
        self.assertEqual(result["task_family"], "summarization")
        self.assertIsNone(result["family_recommendation"])
        self.post.assert_not_called()

    def test_a_call_without_a_family_still_dispatches(self) -> None:
        result = local_generate("q")
        self.assertTrue(result["ok"])
        self.assertEqual(result["routed_by"], "default")
        self.post.assert_called_once()

    def test_quality_best_ask_also_refuses_loudly(self) -> None:
        result = local_generate("q", quality="best", task_family="summarization")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "family_config_error")
        self.post.assert_not_called()


class RealDeclarationRoutingTests(TestCase):
    """One end-to-end pass over the COMMITTED backends.toml + routing-families.toml,
    so the hermetic fixtures above cannot drift away from what ships."""

    def setUp(self) -> None:
        self.enterContext(patch.dict(os.environ, {
            "GOOGLE_CLOUD_PROJECT": "trial-project",
            "GOOGLE_OAUTH_ACCESS_TOKEN": "env-token",
        }, clear=True))
        self.enterContext(patch("hearth.toolsurface.inference.check_occupancy",
                                return_value={"occupancy": "available"}))
        self.post = self.enterContext(patch("hearth.toolsurface.inference._post"))
        self.post.return_value = ({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
                                  None)

    def test_caller_pin_beats_a_deep_quote_retrieval_recommendation(self) -> None:
        result = local_generate("x" * 20000, backend="gcp-gemini",
                                task_family="quote_retrieval")
        self.assertEqual(result["routed_by"], "pinned:gcp-gemini")
        self.assertEqual(result["backend"], "gcp-gemini")
        self.assertEqual(result["task_family"], "quote_retrieval")
        recommendation = result["family_recommendation"]
        self.assertEqual(recommendation["model_id"], "qwen38-27b")
        self.assertEqual(recommendation["backend_hint"], "omen-arc-27b")
        self.assertTrue(recommendation["pin_required"])

    def test_committed_declarations_pin_the_real_depth_specialist(self) -> None:
        """The evidence path this WI exists to open: on the shipped config, a deep
        quote_retrieval call names omen-arc-27b by itself."""
        self.post.return_value = ({"response": "ok"}, None)
        result = local_generate("x" * 20000, task_family="quote_retrieval")
        self.assertEqual(result["routed_by"], "family:quote_retrieval:pinned:omen-arc-27b")
        self.assertEqual(result["backend"], "omen-arc-27b")
        self.assertEqual(result["model"], "qwen38-27b")


class NoVarWritesTests(_HermeticDoor):
    """hearth/var is gateway runtime state: no C-05 code path may write there.

    Stated precisely, because "the suite leaves no hearth/var" is NOT true of
    this repository and never was: at e9822d3 the pristine suite already creates
    the gitignored, empty ``hearth/var/{execution,imagegen,render}`` from the
    default roots in hearth/execution/{ledger,artifacts}.py and
    hearth/imagegen/handoff.py (measured: hearth.tests.toolsurface.test_inference
    creates var/execution, hearth.tests.projection.test_dashboard adds
    var/imagegen). So this test asserts what C-05 can actually own -- that the
    family paths add nothing -- by snapshotting the tree around them instead of
    asserting a global absence some other module would decide.
    """

    @staticmethod
    def _snapshot() -> set:
        var_dir = Path(inference.__file__).resolve().parents[2] / "hearth" / "var"
        if not var_dir.exists():
            return set()
        return {p.relative_to(var_dir).as_posix() for p in var_dir.rglob("*")}

    def test_family_routing_paths_write_nothing_under_hearth_var(self) -> None:
        before = self._snapshot()
        local_generate("q", task_family="reasoning_planning")               # tag route
        local_generate(self.prompt(_DEEP_BYTES), task_family="quote_retrieval")   # pin
        local_generate(self.prompt(_OVER_BUDGET_BYTES),
                       task_family="quote_retrieval")                      # refusal
        local_generate("q", backend="cloud", task_family="document_ocr")   # caller pin
        local_generate("q", quality="best", task_family="summarization")   # ask
        self.assertEqual(self._snapshot(), before)


class ExecutionAdapterPassThroughTests(TestCase):
    """The door lane forwards task_family into the pipeline instead of dropping it."""

    def test_execution_adapter_puts_task_family_in_the_arguments(self) -> None:
        from hearth.observation.identity import DispatchIdentity, dispatch_identity

        captured = {}

        class _FakeService:
            def execute_sync(self, **kwargs):
                captured.update(kwargs)
                return {"ok": True, "text": "ok", "model": "qwen3-30b-a3b"}

        with patch("hearth.execution.defaults.get_execution_service",
                   return_value=_FakeService()):
            with dispatch_identity(DispatchIdentity("claude", "frontier", "omen",
                                                    profile="research")):
                inference._execution_local_generate("q", task_family="summarization")
        self.assertEqual(captured["arguments"]["task_family"], "summarization")

    def test_absent_task_family_is_not_added_to_the_arguments(self) -> None:
        from hearth.observation.identity import DispatchIdentity, dispatch_identity

        captured = {}

        class _FakeService:
            def execute_sync(self, **kwargs):
                captured.update(kwargs)
                return {"ok": True, "text": "ok"}

        with patch("hearth.execution.defaults.get_execution_service",
                   return_value=_FakeService()):
            with dispatch_identity(DispatchIdentity("claude", "frontier", "omen",
                                                    profile="research")):
                inference._execution_local_generate("q")
        self.assertNotIn("task_family", captured["arguments"])


class LedgerShapeTests(_HermeticDoor):
    """The stamps must survive being written down: JSON-serializable, and the
    routed_by string is what the private dashboard counts."""

    def test_family_stamps_are_json_serializable(self) -> None:
        result = local_generate("q", task_family="reasoning_planning")
        round_tripped = json.loads(json.dumps({
            "task_family": result["task_family"],
            "family_recommendation": result["family_recommendation"],
            "routed_by": result["routed_by"],
        }))
        self.assertEqual(round_tripped["family_recommendation"]["family"],
                         "reasoning_planning")
        self.assertTrue(round_tripped["routed_by"].startswith("family:"))
