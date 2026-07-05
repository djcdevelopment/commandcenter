from __future__ import annotations

import os
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase, mock

from hearth.commander.refine import run_refine, DEFAULT_FAN_CRITICS
from hearth.toolsurface import commander


class FakeGen:
    """Scripted local-model stand-in. Routes by prompt role; critic verdicts are
    driven by a per-round, per-critic script. No network."""

    def __init__(self, critic_script: list[list[str]], expand_ok: bool = True) -> None:
        self.critic_script = critic_script
        self.expand_ok = expand_ok
        self.calls: list[tuple[str, str]] = []
        self.round_idx = 0
        self.critic_in_round = 0
        self.revise_count = 0

    def __call__(self, prompt, model=None, backend=None, system=None,
                 max_tokens=None, timeout_s=None):
        self.calls.append((prompt, model, backend))
        if "Write the full proposal now" in prompt:
            if not self.expand_ok:
                return {"ok": False, "error": "cold worker", "model": model}
            return {"ok": True, "text": "DRAFT-0", "model": model,
                    "tokens_out": 10, "duration_ms": 5}
        if "Output only the revised proposal" in prompt:
            self.revise_count += 1
            return {"ok": True, "text": f"DRAFT-{self.revise_count}", "model": model,
                    "tokens_out": 10, "duration_ms": 5}
        if "End your review" in prompt:
            verdicts = self.critic_script[self.round_idx]
            v = verdicts[self.critic_in_round]
            self.critic_in_round += 1
            if self.critic_in_round >= len(verdicts):
                self.critic_in_round = 0
                self.round_idx += 1
            if v == "FAIL":
                return {"ok": False, "error": "cold critic", "model": model}
            return {"ok": True, "text": f"critique text\nVERDICT: {v}", "model": model,
                    "tokens_out": 8, "duration_ms": 4}
        raise AssertionError(f"unexpected prompt role: {prompt[:60]!r}")


class RefineLoopTests(TestCase):
    def test_converges_early_no_revise(self) -> None:
        gen = FakeGen([["CONVERGED"]])
        res = run_refine("an idea", rounds=3, generate=gen)
        self.assertTrue(res["ok"])
        self.assertTrue(res["converged"])
        self.assertEqual(res["rounds_run"], 1)
        self.assertEqual(res["final"], "DRAFT-0")            # never revised
        self.assertEqual(res["cost"]["author_calls"], 1)     # expand only
        self.assertEqual(res["cost"]["critic_calls"], 1)

    def test_runs_full_rounds_when_always_revise(self) -> None:
        gen = FakeGen([["REVISE"], ["REVISE"], ["REVISE"]])
        res = run_refine("an idea", rounds=3, generate=gen)
        self.assertTrue(res["ok"])
        self.assertFalse(res["converged"])
        self.assertEqual(res["rounds_run"], 3)
        self.assertEqual(res["final"], "DRAFT-3")            # revised each round
        self.assertEqual(res["cost"]["author_calls"], 4)     # expand + 3 revises
        self.assertEqual(res["cost"]["critic_calls"], 3)

    def test_fan_calls_all_critics(self) -> None:
        gen = FakeGen([["CONVERGED", "CONVERGED"]])
        res = run_refine("an idea", rounds=2, fan=True, generate=gen)
        self.assertTrue(res["converged"])
        self.assertEqual(len(res["trail"][0]["reviews"]), len(DEFAULT_FAN_CRITICS))
        self.assertEqual(res["cost"]["critic_calls"], 2)

    def test_fan_mixed_verdict_not_converged(self) -> None:
        # round1: one CONVERGED one REVISE -> not converged; round2: both CONVERGED.
        gen = FakeGen([["CONVERGED", "REVISE"], ["CONVERGED", "CONVERGED"]])
        res = run_refine("an idea", rounds=3, fan=True, generate=gen)
        self.assertTrue(res["converged"])
        self.assertEqual(res["rounds_run"], 2)

    def test_author_expand_failure_aborts(self) -> None:
        gen = FakeGen([["CONVERGED"]], expand_ok=False)
        res = run_refine("an idea", rounds=3, generate=gen)
        self.assertFalse(res["ok"])
        self.assertIsNone(res["final"])
        self.assertEqual(res["rounds_run"], 0)
        self.assertIn("author expand failed", res["error"])

    def test_all_critics_fail_stops_without_revise(self) -> None:
        gen = FakeGen([["FAIL"]])
        res = run_refine("an idea", rounds=3, generate=gen)
        self.assertTrue(res["ok"])            # partial run is still ok
        self.assertFalse(res["converged"])
        self.assertEqual(res["rounds_run"], 1)
        self.assertEqual(res["final"], "DRAFT-0")
        self.assertEqual(res["cost"]["failures"], 1)

    def test_fan_one_critic_fails_other_converges(self) -> None:
        gen = FakeGen([["FAIL", "CONVERGED"]])
        res = run_refine("an idea", rounds=2, fan=True, generate=gen)
        # only successful critics gate convergence -> the CONVERGED one converges it
        self.assertTrue(res["converged"])
        self.assertEqual(res["rounds_run"], 1)

    def test_rejects_empty_idea(self) -> None:
        with self.assertRaises(ValueError):
            run_refine("  ", rounds=1, generate=FakeGen([["CONVERGED"]]))


class CommanderProviderTests(TestCase):
    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._prev = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._prev
        shutil.rmtree(self.scope, ignore_errors=True)

    _CANNED = {
        "ok": True, "final": "FINAL TEXT", "rounds_run": 2, "converged": True,
        "cost": {"author_calls": 2, "critic_calls": 2, "tokens_out": 40,
                 "duration_ms": 20, "failures": 0},
        "trail": [{"round": 1, "draft": "d0", "reviews": []}], "error": None,
    }

    def test_persist_and_result_roundtrip(self) -> None:
        stored = commander.persist_refine(self._CANNED, "some idea to refine")
        path = Path(stored["path"])
        self.assertTrue(path.is_file())
        self.assertTrue(path.is_relative_to(self.scope))    # sandboxed
        doc = commander.refine_result(stored["intent_id"])
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["idea"], "some idea to refine")
        self.assertEqual(doc["final"], "FINAL TEXT")
        self.assertEqual(doc["contract_version"], "commander-refine.v1")
        self.assertEqual(len(doc["trail"]), 1)

    def test_refine_idea_digest_shape(self) -> None:
        with mock.patch("hearth.toolsurface.commander.run_refine",
                        return_value=self._CANNED) as rr:
            digest = commander.refine_idea("build me a widget", rounds=2, fan=False)
        rr.assert_called_once()
        self.assertTrue(digest["ok"])
        self.assertTrue(digest["intent_id"].startswith("refine-"))
        self.assertEqual(digest["final"], "FINAL TEXT")
        self.assertTrue(digest["converged"])
        self.assertIn("cost", digest)
        # persisted and retrievable
        self.assertTrue(commander.refine_result(digest["intent_id"])["ok"])

    def test_refine_result_missing(self) -> None:
        res = commander.refine_result("refine-nope-deadbeef")
        self.assertFalse(res["ok"])
        self.assertIn("no refinement", res["error"])

    def test_store_path_rejects_traversal(self) -> None:
        with self.assertRaises(ValueError):
            commander._store_path("../../etc/passwd")

    def test_refine_idea_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            commander.refine_idea("   ")

    def test_get_tools_exports_callables(self) -> None:
        tools = commander.get_tools()
        names = {t.__name__ for t in tools}
        self.assertEqual(names, {"refine_idea", "refine_result"})
