from __future__ import annotations

import os
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase, mock

from hearth.commander import refine
from hearth.commander.refine import (
    DEFAULT_FAN_CRITICS, default_author_seat, default_critic_seats, resolve_defaults,
    run_refine,
)
from hearth.toolsurface import commander
from hearth.toolsurface.backends import Backend, Pool, load_pool


def _pool(*backends: Backend, default: str = "omen-arc") -> Pool:
    return Pool(default=default, backends=tuple(backends))


def _b(name: str, models: list[str], retired: bool = False) -> Backend:
    return Backend(name=name, endpoint=f"http://{name}", api="openai",
                   models=tuple(models), retired=retired)


LIVE_SHAPE = _pool(_b("omen-arc", ["qwen3-30b-a3b"]),
                   _b("fx99-ollama", ["qwen2.5:14b", "qwen2.5:7b"]),
                   _b("omen-ollama", ["qwen3-coder:30b"], retired=True))


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


class CommanderDefaultsTests(TestCase):
    """The commander's defaults must name seats that exist (M2, 2026-09-03)."""

    def test_defaults_resolve_on_the_live_pool(self) -> None:
        pool = load_pool()                                   # the declared file
        author = default_author_seat(pool)
        self.assertEqual(author, ("omen-arc", "qwen3-30b-a3b"))
        self.assertEqual(author[0], pool.default)
        self.assertEqual(author[1], pool.default_backend().models[0])
        resolved = resolve_defaults(fan=True, pool=pool)
        self.assertEqual(resolved["author"], {"backend": "omen-arc", "model": "qwen3-30b-a3b"})
        self.assertEqual(resolved["notes"], [])              # every fan seat is live
        self.assertEqual(len(resolved["critics"]), len(DEFAULT_FAN_CRITICS))
        for seat in resolved["critics"]:
            declared = pool.by_name(seat["backend"])
            self.assertIsNotNone(declared, seat)
            self.assertFalse(declared.retired, seat)
            self.assertIn(seat["model"], declared.models, seat)
            self.assertNotEqual(seat["model"], "qwen3-coder:30b")
            self.assertNotIn("mixtral", seat["model"])
        # non-fan: the author reviews itself, on the same pinned seat
        self.assertEqual(resolve_defaults(fan=False, pool=pool)["critics"],
                         [{"backend": "omen-arc", "model": "qwen3-30b-a3b"}])

    def test_no_default_names_a_retired_ollama_model(self) -> None:
        # The retired names may survive in docstrings (history); never in a literal
        # the loop could dispatch.
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(refine))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        literals = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and n.value not in docstrings]
        for name in ("qwen3-coder:30b", "mixtral"):
            hits = [lit for lit in literals if name in lit]
            self.assertEqual(hits, [], f"{name!r} survives as dispatchable code")

    def test_unpinned_run_dispatches_on_the_default_seat(self) -> None:
        gen = FakeGen([["CONVERGED"]])
        res = run_refine("an idea", rounds=1, generate=gen, pool=LIVE_SHAPE)
        self.assertTrue(res["ok"])
        self.assertEqual(res["seats"]["author"], {"backend": "omen-arc", "model": "qwen3-30b-a3b"})
        self.assertEqual(res["seats"]["critics"], [{"backend": "omen-arc", "model": "qwen3-30b-a3b"}])
        for _, model, backend in gen.calls:                  # expand + self-review
            self.assertEqual((backend, model), ("omen-arc", "qwen3-30b-a3b"))
        self.assertEqual(res["trail"][0]["reviews"][0]["backend"], "omen-arc")

    def test_fan_uses_validated_live_seats(self) -> None:
        gen = FakeGen([["CONVERGED", "CONVERGED"]])
        res = run_refine("an idea", rounds=1, fan=True, generate=gen, pool=LIVE_SHAPE)
        review_seats = [(b, m) for _, m, b in gen.calls[1:]]
        self.assertEqual(review_seats, [("omen-arc", "qwen3-30b-a3b"), ("fx99-ollama", "qwen2.5:14b")])
        self.assertEqual(res["seats"]["notes"], [])

    def test_fan_drops_dead_seats_loudly_and_falls_back_to_self_review(self) -> None:
        pool = _pool(_b("omen-arc", ["qwen3-30b-a3b"]),
                     _b("omen-ollama", ["qwen3-coder:30b"], retired=True))
        seats, notes = default_critic_seats(
            fan=True, pool=pool,
            fan_critics=[("omen-ollama", "qwen3-coder:30b"), ("am4-oxen", "oxen-critic"),
                         ("omen-arc", "nope")])
        self.assertEqual(seats, [("omen-arc", "qwen3-30b-a3b")])
        self.assertEqual(len(notes), 4)
        self.assertIn("retired", notes[0])
        self.assertIn("not declared", notes[1])
        self.assertIn("model not served", notes[2])
        self.assertIn("falling back to author self-review", notes[3])
        # the run surfaces the same notes on its result — a shrunk fan is never silent
        gen = FakeGen([["CONVERGED"]])
        res = run_refine("an idea", rounds=1, fan=True, generate=gen, pool=pool,
                         fan_critics=[("am4-oxen", "oxen-critic")])
        self.assertEqual(res["seats"]["critics"], [{"backend": "omen-arc", "model": "qwen3-30b-a3b"}])
        self.assertTrue(any("falling back" in n for n in res["seats"]["notes"]))

    def test_legacy_bare_model_fan_critics_stay_unpinned(self) -> None:
        gen = FakeGen([["CONVERGED", "CONVERGED"]])
        res = run_refine("an idea", rounds=1, fan=True, generate=gen, pool=LIVE_SHAPE,
                         fan_critics=["qwen2.5:7b", ("fx99-ollama", "qwen2.5:14b")])
        self.assertEqual(res["seats"]["critics"],
                         [{"backend": None, "model": "qwen2.5:7b"},
                          {"backend": "fx99-ollama", "model": "qwen2.5:14b"}])

    def test_pinned_backend_without_model_takes_that_rungs_first_model(self) -> None:
        gen = FakeGen([["CONVERGED"]])
        res = run_refine("an idea", rounds=1, generate=gen, pool=LIVE_SHAPE,
                         author_backend="fx99-ollama")
        self.assertEqual(res["seats"]["author"], {"backend": "fx99-ollama", "model": "qwen2.5:14b"})
        res = run_refine("an idea", rounds=1, generate=FakeGen([["CONVERGED"]]),
                         pool=LIVE_SHAPE, author_backend="not-declared")
        self.assertEqual(res["seats"]["author"], {"backend": "not-declared", "model": None})
        self.assertTrue(any("not declared" in n for n in res["seats"]["notes"]))

    def test_explicit_pins_are_untouched(self) -> None:
        gen = FakeGen([["CONVERGED"]])
        res = run_refine("an idea", rounds=1, generate=gen, pool=LIVE_SHAPE,
                         author_backend="omen-swap", author_model="phi4-vk1",
                         critic_specs=[("gcp-gemini", "gemini-3.5-flash")])
        self.assertEqual(res["seats"]["author"], {"backend": "omen-swap", "model": "phi4-vk1"})
        self.assertEqual(res["seats"]["critics"], [{"backend": "gcp-gemini", "model": "gemini-3.5-flash"}])
        self.assertEqual(res["seats"]["notes"], [])


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
