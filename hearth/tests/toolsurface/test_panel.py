"""Held-out judge panel (M2): no seat scores the rung or model it sits on."""
from __future__ import annotations

from unittest import TestCase

from hearth.experiments import panel
from hearth.experiments.panel import (
    JUDGE_POOL, Panel, PanelConflict, assert_held_out, coerce_seat, held_out_judges,
)
from hearth.toolsurface.backends import Backend, Pool, load_pool


def _b(name: str, models: list[str], node: str | None = None, hw: str | None = None,
       retired: bool = False) -> Backend:
    settings: dict = {}
    if node:
        settings["node"] = node
    if hw:
        settings["hardware_profile_id"] = hw
    return Backend(name=name, endpoint=f"http://{name}", api="openai",
                   models=tuple(models), retired=retired, settings=settings)


def _fake_pool() -> Pool:
    """The live pool's shape (names, models, nodes, tombstones) without the file."""
    return Pool(default="omen-arc", backends=(
        _b("omen-arc", ["qwen3-30b-a3b"], "omen", "omen-285k-dual-b70-2026H2"),
        _b("omen-arc-27b", ["qwen38-27b"], "omen", "omen-285k-dual-b70-2026H2"),
        _b("omen-swap", ["phi4-vk1", "phi4-vk2", "qwen14b-vk1", "qwen14b-vk2",
                         "qwen38-27b-dual"], "omen", "omen-285k-dual-b70-2026H2"),
        _b("fx99-ollama", ["qwen2.5:14b", "qwen2.5:7b"], "fx99", "fx99-2070super-2026H2"),
        _b("gcp-gemini", ["gemini-3.5-flash"], "gcp-vertex"),          # no hardware id
        _b("gcp-gemini-pro", ["gemini-3.1-pro-preview"], "gcp-vertex"),
        _b("omen-ollama", ["qwen3-coder:30b"], "omen", "omen-285k-dual-b70-2026H2",
           retired=True),
    ))


class SeatCoercionTests(TestCase):
    def test_accepts_every_arm_shape(self) -> None:
        self.assertEqual(coerce_seat(("gcp-gemini", "gemini-3.5-flash")),
                         ("gcp-gemini", "gemini-3.5-flash"))
        self.assertEqual(coerce_seat(["omen-swap", "phi4-vk1"]), ("omen-swap", "phi4-vk1"))
        self.assertEqual(coerce_seat("gcp-gemini"), ("gcp-gemini", None))   # doc-bench arm
        self.assertEqual(coerce_seat({"backend": "omen-arc", "model": "qwen3-30b-a3b"}),
                         ("omen-arc", "qwen3-30b-a3b"))
        self.assertEqual(coerce_seat(("label", "fx99-ollama", "qwen2.5:14b")),
                         ("fx99-ollama", "qwen2.5:14b"))                     # rejudge_panel shape

        class RoleLike:
            backend, model = None, "qwen3-coder:30b"

        self.assertEqual(coerce_seat(RoleLike()), (None, "qwen3-coder:30b"))
        with self.assertRaises(ValueError):
            coerce_seat(("only-one",))


class HeldOutTests(TestCase):
    def setUp(self) -> None:
        self.pool = _fake_pool()

    def test_gcp_gemini_dropped_when_it_is_an_arm(self) -> None:
        # The 2026-08-29 doc bench: gcp-gemini was an arm AND a judge seat. The
        # bench names arms by backend only — that shape must be enough to drop it.
        p = held_out_judges(["gcp-gemini", "omen-arc"], pool=self.pool)
        self.assertNotIn("gcp-gemini", [b for b, _ in p.judges])
        self.assertNotIn("omen-arc", [b for b, _ in p.judges])
        self.assertEqual(p.judges, (("fx99-ollama", "qwen2.5:14b"),
                                    ("gcp-gemini-pro", "gemini-3.1-pro-preview")))
        dropped = {e["backend"]: e["reason"] for e in p.excluded}
        self.assertIn("shares backend 'gcp-gemini'", dropped["gcp-gemini"])
        self.assertIn("arm gcp-gemini/*", dropped["gcp-gemini"])
        self.assertIn("shares backend 'omen-arc'", dropped["omen-arc"])
        self.assertIsNone(p.panel_note)          # gcp-vertex declares no hardware to share

    def test_three_arm_bench_cannot_seat_two_and_says_so(self) -> None:
        arms = [("gcp-gemini", "gemini-3.5-flash"),
                ("gcp-gemini-pro", "gemini-3.1-pro-preview"),
                ("omen-arc", "qwen3-30b-a3b")]
        with self.assertRaises(PanelConflict) as cm:
            held_out_judges(arms, pool=self.pool)
        exc = cm.exception
        self.assertEqual(exc.judges, (("fx99-ollama", "qwen2.5:14b"),))
        self.assertEqual(exc.min_seats, 2)
        self.assertEqual({e["backend"] for e in exc.excluded},
                         {"gcp-gemini", "gcp-gemini-pro", "omen-arc"})
        self.assertIn("below min_seats=2", str(exc))
        # a deliberate one-seat panel is allowed, but it has to be asked for
        p = held_out_judges(arms, min_seats=1, pool=self.pool)
        self.assertEqual(p.judges, (("fx99-ollama", "qwen2.5:14b"),))

    def test_model_conflict_across_backends_and_placement_siblings(self) -> None:
        # same model served by another rung name is still self-judging; so is the
        # llama-swap sibling entry (same weights, other card)
        seats = [("fx99-ollama", "qwen2.5:14b"), ("omen-swap", "phi4-vk2"),
                 ("gcp-gemini", "gemini-3.5-flash")]
        p = held_out_judges([("omen-swap", "phi4-vk1"), ("some-other-rung", "qwen2.5:14b")],
                            seats, min_seats=1, pool=self.pool)
        self.assertEqual(p.judges, (("gcp-gemini", "gemini-3.5-flash"),))
        reasons = {e["backend"]: e["reason"] for e in p.excluded}
        self.assertIn("shares model 'qwen2.5:14b'", reasons["fx99-ollama"])
        self.assertIn("shares backend 'omen-swap'", reasons["omen-swap"])
        # the placement-sibling rule on its own: phi4-vk2 IS phi4-vk1 (same weights,
        # other card, ADR-0042), and -dual is the same model on both cards
        with self.assertRaises(PanelConflict) as cm:
            assert_held_out([("fx99-ollama", "phi4-vk2")], [("omen-swap", "phi4-vk1")],
                            pool=self.pool)
        self.assertIn("shares model 'phi4-vk2'", str(cm.exception))
        with self.assertRaises(PanelConflict):
            assert_held_out([("fx99-ollama", "qwen38-27b-dual")], [("omen-arc-27b", "qwen38-27b")],
                            pool=self.pool)
        # ...but a different model on a different rung is held out
        assert_held_out([("fx99-ollama", "qwen2.5:7b")], [("omen-swap", "phi4-vk1")],
                        pool=self.pool)

    def test_unpinned_arm_resolves_to_the_door_default(self) -> None:
        # Role(None, ...) actually runs on the pool default, so an omen-arc seat is
        # NOT held out from it even though the names differ.
        p = held_out_judges([(None, "anything")], pool=self.pool)
        self.assertNotIn("omen-arc", [b for b, _ in p.judges])
        self.assertIn("shares backend 'omen-arc'",
                      next(e["reason"] for e in p.excluded if e["backend"] == "omen-arc"))

    def test_same_node_co_residency_is_a_note_not_an_exclusion(self) -> None:
        # M1's pour: two side models on omen-swap. omen-arc is a different rung
        # and model, so it stays seated — but it is the same two B70s.
        p = held_out_judges([("omen-swap", "phi4-vk1"), ("omen-swap", "qwen14b-vk1")],
                            pool=self.pool)
        self.assertIn(("omen-arc", "qwen3-30b-a3b"), p.judges)
        self.assertEqual(len(p.judges), 4)
        self.assertEqual([c["backend"] for c in p.co_resident], ["omen-arc"])
        self.assertEqual(p.co_resident[0]["node"], "omen")
        self.assertEqual(p.co_resident[0]["arm"], "omen-swap/phi4-vk1")
        self.assertIsNotNone(p.panel_note)
        self.assertIn("omen-arc/qwen3-30b-a3b beside omen-swap/phi4-vk1 on 'omen'", p.panel_note)
        self.assertIn("ADR-0041", p.panel_note)
        self.assertIn("never concurrently", p.panel_note)
        self.assertEqual(p.as_dict()["panel_note"], p.panel_note)

    def test_retired_and_undeclared_seats_are_dropped_with_reasons(self) -> None:
        seats = [("omen-ollama", "qwen3-coder:30b"), ("am4-oxen", "oxen-critic"),
                 ("fx99-ollama", "qwen2.5:14b"), ("gcp-gemini", "gemini-3.5-flash"),
                 (None, "qwen2.5:7b")]
        p = held_out_judges([("omen-swap", "phi4-vk1")], seats, pool=self.pool)
        # the unpinned seat resolves to the default rung and then faces the rule
        # like any other; here it lands on omen-arc, which is held out from the arm
        self.assertEqual(p.judges, (("fx99-ollama", "qwen2.5:14b"),
                                    ("gcp-gemini", "gemini-3.5-flash"),
                                    ("omen-arc", "qwen2.5:7b")))
        reasons = {(e["backend"], e["model"]): e["reason"] for e in p.excluded}
        self.assertIn("retired", reasons[("omen-ollama", "qwen3-coder:30b")])
        self.assertIn("not declared", reasons[("am4-oxen", "oxen-critic")])

    def test_max_seats_caps_from_the_front_in_preference_order(self) -> None:
        p = held_out_judges([("omen-swap", "phi4-vk1")], max_seats=2, pool=self.pool)
        self.assertEqual(p.judges, (JUDGE_POOL[0], JUDGE_POOL[1]))

    def test_panel_is_iterable_as_judge_pairs(self) -> None:
        p = held_out_judges([("omen-swap", "phi4-vk1")], pool=self.pool)
        self.assertIsInstance(p, Panel)
        self.assertEqual(list(p), list(p.judges))
        self.assertEqual(len(p), len(p.judges))
        for jb, jm in p:            # the exact loop score_proposal runs
            self.assertIsInstance(jb, str)
            self.assertIsInstance(jm, str)

    def test_rejects_empty_arms_and_bad_min_seats(self) -> None:
        with self.assertRaises(ValueError):
            held_out_judges([], pool=self.pool)
        with self.assertRaises(ValueError):
            held_out_judges([("omen-swap", "phi4-vk1")], min_seats=0, pool=self.pool)

    def test_unreadable_pool_is_noted_not_fatal(self) -> None:
        class Broken:
            @property
            def backends(self):
                raise RuntimeError("no pool")

        p = held_out_judges([("omen-swap", "phi4-vk1")], pool=Broken())
        self.assertEqual(len(p.judges), 4)
        self.assertTrue(any("pool declaration unreadable" in n for n in p.notes))


class AssertHeldOutTests(TestCase):
    def setUp(self) -> None:
        self.pool = _fake_pool()

    def test_passes_a_held_out_panel(self) -> None:
        assert_held_out([("fx99-ollama", "qwen2.5:14b"), ("gcp-gemini", "gemini-3.5-flash")],
                        [("omen-swap", "phi4-vk1"), (None, "qwen3-30b-a3b")], pool=self.pool)

    def test_raises_on_backend_or_model_overlap(self) -> None:
        with self.assertRaises(PanelConflict) as cm:
            assert_held_out([("gcp-gemini", "gemini-3.5-flash")],
                            [("gcp-gemini", "gemini-3.5-flash")], pool=self.pool)
        self.assertIn("self-judging panel", str(cm.exception))
        self.assertIn("held_out_judges(arms)", str(cm.exception))
        with self.assertRaises(PanelConflict):
            assert_held_out([(None, "qwen3-coder:30b")], [(None, "qwen3-coder:30b")],
                            pool=self.pool)
        # unpinned judge == the default rung == a pinned omen-arc arm
        with self.assertRaises(PanelConflict):
            assert_held_out([(None, "qwen2.5:14b")], [("omen-arc", "qwen3-30b-a3b")],
                            pool=self.pool)


class LivePoolTests(TestCase):
    """JUDGE_POOL against the declared pool file: every seat must be able to serve."""

    def test_judge_pool_names_live_declared_seats(self) -> None:
        pool = load_pool()
        for backend, model in JUDGE_POOL:
            with self.subTest(seat=f"{backend}/{model}"):
                declared = pool.by_name(backend)
                self.assertIsNotNone(declared, f"{backend} not declared")
                self.assertFalse(declared.retired, f"{backend} is retired")
                self.assertIn(model, declared.models)

    def test_judge_pool_holds_no_retired_ollama_names(self) -> None:
        for _, model in JUDGE_POOL:
            self.assertNotEqual(model, "qwen3-coder:30b")
            self.assertNotIn("mixtral", model)

    def test_live_pool_drops_gemini_arm_and_notes_omen_co_residency(self) -> None:
        p = held_out_judges(["gcp-gemini", ("omen-swap", "phi4-vk1")])   # real pool file
        self.assertNotIn("gcp-gemini", [b for b, _ in p.judges])
        self.assertIn(("omen-arc", "qwen3-30b-a3b"), p.judges)
        self.assertIn("omen-arc", [c["backend"] for c in p.co_resident])
        self.assertIn("'omen'", p.panel_note)
        self.assertEqual(p.notes, ())

    def test_module_is_kernel_free(self) -> None:
        import inspect
        source = inspect.getsource(panel)
        self.assertNotIn("hearth.kernel", source)
