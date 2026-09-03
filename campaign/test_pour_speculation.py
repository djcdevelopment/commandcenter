"""pour_speculation tests (M3, token hole #1).

Nothing here ever reaches the conductor: the real submit_task is never
called -- main() takes an injected submit_fn, and the dry-run test injects
one that raises if touched.
"""
from __future__ import annotations

import inspect
import json
import tempfile
from pathlib import Path
from unittest import TestCase

from campaign import pour_speculation as pour
from hearth.toolsurface.task_lane import DEFAULT_BUILDERS, estimate_tokens

DEAD_BUILDERS = ("omen-worker-1", "am4-worker-1")


def _never_submit(*args, **kwargs) -> dict:
    raise AssertionError("dry-run must never submit")


class PlanIdeasTests(TestCase):
    def test_every_idea_targets_the_task_lanes_default_builders(self) -> None:
        plans = pour.plan_ideas()
        self.assertEqual(len(plans), len(pour.IDEAS))
        for plan in plans:
            with self.subTest(slug=plan["slug"]):
                self.assertEqual(plan["builders"], DEFAULT_BUILDERS)
                self.assertGreaterEqual(len(plan["builders"]), 2)  # conductor fan-out minimum

    def test_no_dead_builder_is_named_anywhere_in_the_module(self) -> None:
        # The old PAIRS table rotated omen-worker-1 / am4-worker-1, both aimed
        # at dead backends by 2026-08-29. The campaign must follow the task
        # lane's roster, not carry its own.
        source = inspect.getsource(pour)
        self.assertFalse(hasattr(pour, "PAIRS"))
        for plan in pour.plan_ideas():
            for dead in DEAD_BUILDERS:
                self.assertNotIn(dead, plan["builders"])
        # The names may only survive in the comment explaining why they left.
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for dead in DEAD_BUILDERS:
                self.assertNotIn(dead, stripped, f"dead builder in code: {line!r}")

    def test_every_idea_carries_a_derived_est_tokens_and_task_class(self) -> None:
        for plan in pour.plan_ideas():
            with self.subTest(slug=plan["slug"]):
                self.assertEqual(plan["task_class"], "research")
                self.assertIsInstance(plan["est_tokens"], int)
                self.assertGreater(plan["est_tokens"], 0)
                self.assertEqual(plan["est_tokens"],
                                 estimate_tokens(plan["body"], "research"))
                self.assertIn(f"proposals/{plan['slug']}.md", plan["body"])

    def test_plan_is_pure_and_repeatable(self) -> None:
        self.assertEqual(pour.plan_ideas(), pour.plan_ideas())

    def test_builders_override_is_honoured(self) -> None:
        plans = pour.plan_ideas(builders=["cc-builder-1", "cc-builder-2"])
        self.assertTrue(all(p["builders"] == ["cc-builder-1", "cc-builder-2"] for p in plans))


class MainTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.out = self.tmp / "manifest.json"

    def test_dry_run_shows_est_tokens_per_idea_and_submits_nothing(self) -> None:
        rc = pour.main(["--dry-run", "--out", str(self.out)], submit_fn=_never_submit)
        self.assertEqual(rc, 0)
        manifest = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(manifest["count"], len(pour.IDEAS))
        for idea in manifest["ideas"]:
            with self.subTest(slug=idea["slug"]):
                self.assertIsNone(idea["plan_id"])
                self.assertEqual(idea["builders"], DEFAULT_BUILDERS)
                self.assertEqual(idea["task_class"], "research")
                self.assertGreater(idea["est_tokens"], 0)

    def test_live_path_passes_task_class_and_est_tokens_to_submit(self) -> None:
        calls: list[dict] = []

        def fake_submit(prompt, builders=None, plan_id_hint=None, task_class=None,
                        est_tokens=None):
            calls.append(dict(prompt=prompt, builders=builders, plan_id_hint=plan_id_hint,
                              task_class=task_class, est_tokens=est_tokens))
            return {"ok": True, "plan_id": f"hearth-{plan_id_hint}-deadbeef",
                    "builders": builders, "est_tokens": est_tokens,
                    "est_tokens_source": "caller"}

        rc = pour.main(["--out", str(self.out)], submit_fn=fake_submit)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), len(pour.IDEAS))
        for call, plan in zip(calls, pour.plan_ideas()):
            with self.subTest(slug=plan["slug"]):
                self.assertEqual(call["task_class"], "research")
                self.assertEqual(call["est_tokens"], plan["est_tokens"])
                self.assertEqual(call["builders"], DEFAULT_BUILDERS)
                self.assertEqual(call["plan_id_hint"], f"spec-{plan['slug']}")
        manifest = json.loads(self.out.read_text(encoding="utf-8"))
        for idea, plan in zip(manifest["ideas"], pour.plan_ideas()):
            self.assertTrue(idea["ok"])
            self.assertEqual(idea["est_tokens"], plan["est_tokens"])
            self.assertEqual(idea["est_tokens_source"], "caller")
            self.assertTrue(idea["plan_id"].startswith("hearth-spec-"))

    def test_default_manifest_path_is_the_tracked_campaign_file(self) -> None:
        self.assertTrue(pour.DEFAULT_MANIFEST_PATH.endswith("speculation_manifest.json"))
