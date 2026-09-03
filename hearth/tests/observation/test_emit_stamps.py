"""P8 — the capacity observation carries the dispatch stamps in `notes`.

The capacity-observation.v1 schema is closed, so the rung's health at dispatch and
the pool declaration that routed the call travel as text on the one open field.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.health.rungstate import NOTE, summarize_for_notes
from hearth.observation.emit import build_observation, emit_dispatch_observation
from hearth.observation.identity import DispatchIdentity

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

IDENTITY = DispatchIdentity(caller_id="claude-frontier", runner_class="frontier",
                           node="omen", task_id="task-1", profile="unrestricted")
SETTINGS = {"node": "omen", "hardware_profile_id": "omen-285k-dual-b70-2026H2"}

DEGRADED = {
    "rung": "omen-arc", "port": 8082, "verdict": "degraded",
    "baseline_tok_s": 106.0, "baseline_epoch": "2026-08-29T18:22 incumbent epoch",
    "envelope": {"fail_below": 0.8, "warn_below": 0.9},
    "observed_tok_s": 65.0, "observed_at": "2026-09-03T08:00:00-07:00",
    "observed_age_s": 41.0, "frac_of_baseline": 0.6132, "prefill_stall_recent": False,
    "last_ping_ok": True, "deep_samples": 3, "excluded_windows": ["rot-cutover-20260903"],
    "note": NOTE,
}


def ok_result(**overrides) -> dict:
    result = {
        "ok": True, "text": "a real answer", "model": "qwen3-30b-a3b",
        "backend": "omen-arc", "endpoint": "http://127.0.0.1:8082",
        "routed_by": "pinned:omen-arc", "occupancy": "available", "error": None,
        "duration_ms": 2000.0, "tokens_in": 1200, "tokens_out": 400, "max_tokens": 4096,
    }
    result.update(overrides)
    return result


def _observe(result: dict) -> dict:
    return build_observation(
        result=result, endpoint=result["endpoint"], backend=result["backend"],
        model=result["model"], settings=SETTINGS, task=None, payload_bytes=4800,
        resolved_max_tokens=4096, identity=IDENTITY)


class NotesStampTests(TestCase):
    def test_rung_state_and_pool_hash_ride_notes(self) -> None:
        observation = _observe(ok_result(rung_state=DEGRADED, pool_config_hash="0123abcdef45"))
        notes = observation["workload_shape"]["notes"]

        self.assertIn(f"rung_state: {summarize_for_notes(DEGRADED)}", notes)
        self.assertIn("omen-arc degraded 65.0/106.0 tok/s 61% of epoch", notes)
        self.assertIn("excl rot-cutover-20260903", notes)
        self.assertIn("pool=0123abcdef45", notes)
        # The pre-existing carriers are untouched and still lead.
        self.assertTrue(notes.startswith("routed_by=pinned:omen-arc; occupancy=available"))

    def test_notes_split_cleanly_on_the_joiner(self) -> None:
        """summarize_for_notes emits no ';' so the stamp is one note, not several."""
        observation = _observe(ok_result(rung_state=DEGRADED, pool_config_hash="0123abcdef45"))
        parts = observation["workload_shape"]["notes"].split("; ")
        stamped = [p for p in parts if p.startswith("rung_state: ")]
        self.assertEqual(len(stamped), 1)
        self.assertLessEqual(len(stamped[0]) - len("rung_state: "), 96)

    def test_unstamped_result_leaves_notes_unchanged(self) -> None:
        notes = _observe(ok_result())["workload_shape"]["notes"]
        self.assertNotIn("rung_state", notes)
        self.assertNotIn("pool=", notes)
        self.assertEqual(notes, "routed_by=pinned:omen-arc; occupancy=available; "
                                "runtime_s is wall-clock")

    def test_none_stamps_are_absence(self) -> None:
        """A cloud dispatch stamps rung_state=None: no health line about OMEN appears."""
        notes = _observe(ok_result(rung_state=None, pool_config_hash=None))["workload_shape"]["notes"]
        self.assertNotIn("rung_state", notes)
        self.assertNotIn("pool=", notes)

    def test_non_dict_rung_state_is_skipped_not_guessed(self) -> None:
        notes = _observe(ok_result(rung_state="degraded", pool_config_hash=""))["workload_shape"]["notes"]
        self.assertNotIn("rung_state", notes)
        self.assertNotIn("pool=", notes)

    def test_stamped_observation_is_still_structurally_valid(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "tests" / "workflow"))
        from test_capacity_observation_schema import structural_problems

        observation = _observe(ok_result(rung_state=DEGRADED, pool_config_hash="0123abcdef45"))
        self.assertEqual(structural_problems(observation), [])
        # The closed schema gained no key: the stamps live only in notes.
        self.assertNotIn("rung_state", observation)
        self.assertNotIn("pool_config_hash", observation)


class EmittedArtifactTests(TestCase):
    def setUp(self) -> None:
        self.root = Path(mkdtemp()).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_written_artifact_carries_the_stamps(self) -> None:
        result = ok_result(rung_state=DEGRADED, pool_config_hash="0123abcdef45")
        emitted = emit_dispatch_observation(
            result=result, endpoint=result["endpoint"], backend=result["backend"],
            model=result["model"], settings=SETTINGS, task=None, payload_bytes=4800,
            resolved_max_tokens=4096, identity=IDENTITY, root=self.root)
        self.assertTrue(emitted["emitted"])

        document = json.loads(Path(emitted["path"]).read_text(encoding="utf-8"))
        notes = document["workload_shape"]["notes"]
        self.assertIn("rung_state: omen-arc degraded", notes)
        self.assertIn("pool=0123abcdef45", notes)
        self.assertEqual(document["outcome"], "success")
