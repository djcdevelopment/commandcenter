"""The three sources: ordering, reporting, atomic idempotent moves, the bug fix.

Every path in this file is a temp directory. Nothing here reads or writes the
live ``hearth/var`` backlog, the live commander refine store, or the repo's
``knowledge/*.json`` — the last of those is asserted by construction (the
candidate source takes both paths as required arguments).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase

from hearth.backlog import sources
from hearth.backlog.briefs import Brief


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_json(path: Path, data) -> Path:
    return _write(path, json.dumps(data))


def _brief_file(body: str, *, builders=("cc-builder-2",), **meta) -> str:
    from hearth.toolsurface.task_lane import _ccmeta_header
    return _ccmeta_header(list(builders), **meta) + body


_GOOD_SIDECAR = {
    "contract": sources.PROMOTE_CONTRACT,
    "task_class": "build",
    "requires": ["docs/plan.md"],
    "max_age_s": 3600,
    "builders": None,
    "promoted_by": "derek",
    "promoted_at": "2026-09-06T10:00:00Z",
}


def _refine_doc(intent_id: str, final: str = "Build the thing carefully.") -> dict:
    return {
        "contract_version": "commander-refine.v1",
        "intent_id": intent_id,
        "mode": "refine",
        "created": "2026-09-05T09:00:00Z",
        "idea": "build the thing",
        "final": final,
        "rounds_run": 3,
        "converged": True,
        "trail": [],
        "ok": True,
    }


class AuthoredSourceTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.queued = self.tmp / "queued"
        self.dispatched = self.tmp / "dispatched"
        self.done = self.tmp / "done"
        self.queued.mkdir(parents=True)

    def test_absent_directory_is_an_empty_scan_and_creates_nothing(self) -> None:
        missing = self.tmp / "nope"
        scan = sources.authored_source(missing)
        self.assertEqual(len(scan), 0)
        self.assertFalse(missing.exists(), "a read must never create its own directory")

    def test_oldest_mtime_first_with_filename_tie_break(self) -> None:
        # mtimes set EXPLICITLY: the FS clock is coarser than this loop, so
        # relying on write order would make the ordering assertion accidental.
        for name in ("c-newest.md", "a-tie.md", "b-tie.md", "d-oldest.md"):
            _write(self.queued / name, _brief_file(f"body of {name}"))
        os.utime(self.queued / "d-oldest.md", (1000, 1000))
        os.utime(self.queued / "a-tie.md", (2000, 2000))
        os.utime(self.queued / "b-tie.md", (2000, 2000))
        os.utime(self.queued / "c-newest.md", (3000, 3000))
        order = [b.source_ref for b in sources.authored_source(self.queued)]
        self.assertEqual(order, ["d-oldest.md", "a-tie.md", "b-tie.md", "c-newest.md"])

    def test_unparseable_file_is_skipped_and_reported_not_raised(self) -> None:
        _write(self.queued / "good.md", _brief_file("a real brief"))
        _write(self.queued / "bad.md", "no header at all")
        scan = sources.authored_source(self.queued)
        self.assertEqual([b.source_ref for b in scan], ["good.md"])
        self.assertEqual([r["source_ref"] for r in scan.rejected], ["bad.md"])
        self.assertIn("unparseable", scan.rejected[0]["reason"])

    def test_file_removed_between_listing_and_parsing_is_skipped(self) -> None:
        # Fault injection: the glob has already named the file when it vanishes.
        _write(self.queued / "keep.md", _brief_file("kept"))
        vanishing = _write(self.queued / "gone.md", _brief_file("doomed"))
        real_read = Path.read_text

        def read_text(self_path, *args, **kwargs):
            if self_path.name == "gone.md":
                raise FileNotFoundError(2, "No such file or directory", str(self_path))
            return real_read(self_path, *args, **kwargs)

        Path.read_text = read_text
        try:
            scan = sources.authored_source(self.queued)
        finally:
            Path.read_text = real_read
        self.assertEqual([b.source_ref for b in scan], ["keep.md"])
        self.assertEqual([r["source_ref"] for r in scan.rejected], ["gone.md"])
        self.assertIn("unreadable", scan.rejected[0]["reason"])
        self.assertTrue(vanishing.exists())

    def test_brief_carries_the_filename_as_source_ref_and_stem_as_slug(self) -> None:
        _write(self.queued / "0001-do_the_thing.md", _brief_file("body"))
        brief = sources.authored_source(self.queued).briefs[0]
        self.assertEqual(brief.source, "authored")
        self.assertEqual(brief.source_ref, "0001-do_the_thing.md")
        self.assertEqual(brief.slug, "0001-do_the_thing")

    # -- moves --------------------------------------------------------------

    def _one_brief(self) -> Brief:
        _write(self.queued / "0001-thing.md", _brief_file("body"))
        return sources.authored_source(self.queued).briefs[0]

    def test_mark_dispatched_moves_the_file(self) -> None:
        brief = self._one_brief()
        result = sources.mark_dispatched(brief, "hearth-authored-0001-thing-abcd1234",
                                         self.queued, self.dispatched)
        self.assertEqual(result["status"], "moved")
        self.assertFalse((self.queued / "0001-thing.md").exists())
        self.assertTrue((self.dispatched / "hearth-authored-0001-thing-abcd1234.md").is_file())

    def test_mark_dispatched_twice_is_exactly_one_logical_move(self) -> None:
        brief = self._one_brief()
        plan_id = "hearth-authored-0001-thing-abcd1234"
        first = sources.mark_dispatched(brief, plan_id, self.queued, self.dispatched)
        second = sources.mark_dispatched(brief, plan_id, self.queued, self.dispatched)
        self.assertEqual(first["status"], "moved")
        self.assertEqual(second["status"], "already_moved")
        self.assertFalse(second["moved"])
        self.assertEqual(len(list(self.dispatched.glob("*.md"))), 1)

    def test_mark_dispatched_leaves_the_original_intact_when_the_replace_fails(self) -> None:
        # Atomicity: os.replace is the only mutating step, so a crash BEFORE it
        # must leave the queued file byte-intact and the target absent.
        brief = self._one_brief()
        original = (self.queued / "0001-thing.md").read_bytes()

        def boom(src, dst):
            raise OSError(5, "simulated device failure")

        with self.assertRaises(OSError):
            sources.mark_dispatched(brief, "hearth-x-1", self.queued, self.dispatched,
                                    replace_fn=boom)
        self.assertEqual((self.queued / "0001-thing.md").read_bytes(), original)
        self.assertEqual(list(self.dispatched.glob("*.md")), [])

    def test_mark_dispatched_rejects_a_source_ref_outside_the_root(self) -> None:
        brief = self._one_brief()
        escaped = Brief(**{**brief.__dict__, "source_ref": "../outside.md"})
        with self.assertRaises(ValueError) as ctx:
            sources.mark_dispatched(escaped, "hearth-x-1", self.queued, self.dispatched)
        self.assertIn("escapes the backlog root", str(ctx.exception))

    def test_mark_dispatched_rejects_an_unsafe_plan_id(self) -> None:
        brief = self._one_brief()
        for bad in ("../evil", "with space", "a/b"):
            with self.subTest(plan_id=bad), self.assertRaises(ValueError):
                sources.mark_dispatched(brief, bad, self.queued, self.dispatched)

    def test_mark_done_moves_and_is_idempotent(self) -> None:
        brief = self._one_brief()
        plan_id = "hearth-authored-0001-thing-abcd1234"
        sources.mark_dispatched(brief, plan_id, self.queued, self.dispatched)
        first = sources.mark_done(plan_id, self.dispatched, self.done)
        second = sources.mark_done(plan_id, self.dispatched, self.done)
        self.assertEqual(first["status"], "moved")
        self.assertEqual(second["status"], "already_moved")
        self.assertEqual(len(list(self.done.glob("*.md"))), 1)

    def test_mark_done_on_a_plan_that_was_never_dispatched_reports_missing(self) -> None:
        self.assertEqual(
            sources.mark_done("hearth-never-1", self.dispatched, self.done)["status"],
            "missing")


class RefinedSourceTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.refine = self.tmp / "refine"
        self.refine.mkdir(parents=True)

    def _intent(self, intent_id: str, *, sidecar=None, final="Build the thing carefully.") -> None:
        _write_json(self.refine / f"{intent_id}.json", _refine_doc(intent_id, final))
        if sidecar is not None:
            _write_json(self.refine / f"{intent_id}{sources.PROMOTE_SUFFIX}", sidecar)

    def test_only_promoted_intents_yield(self) -> None:
        self._intent("refine-alpha-11111111", sidecar=_GOOD_SIDECAR)
        self._intent("refine-beta-22222222")
        scan = sources.refined_source(self.refine)
        self.assertEqual([b.source_ref for b in scan], ["refine-alpha-11111111"])
        self.assertEqual(
            [(r["source_ref"], r["reason"]) for r in scan.rejected],
            [("refine-beta-22222222", "not-promoted")])

    def test_brief_body_is_the_refine_results_final_text(self) -> None:
        self._intent("refine-alpha-11111111", sidecar=_GOOD_SIDECAR,
                     final="THE FINAL TEXT")
        brief = sources.refined_source(self.refine).briefs[0]
        self.assertEqual(brief.body, "THE FINAL TEXT")
        self.assertEqual(brief.source, "refined")
        self.assertEqual(brief.task_class, "build")
        self.assertEqual(brief.requires, ("docs/plan.md",))
        self.assertEqual(brief.max_age_s, 3600)

    def test_ordered_by_oldest_promotion_then_intent_id(self) -> None:
        self._intent("refine-c-33333333",
                     sidecar={**_GOOD_SIDECAR, "promoted_at": "2026-09-06T12:00:00Z"})
        self._intent("refine-b-22222222",
                     sidecar={**_GOOD_SIDECAR, "promoted_at": "2026-09-06T09:00:00Z"})
        self._intent("refine-a-11111111",
                     sidecar={**_GOOD_SIDECAR, "promoted_at": "2026-09-06T09:00:00Z"})
        self.assertEqual([b.source_ref for b in sources.refined_source(self.refine)],
                         ["refine-a-11111111", "refine-b-22222222", "refine-c-33333333"])

    def test_sidecar_contract_is_enforced_field_by_field(self) -> None:
        bad_sidecars = {
            "wrong contract": {**_GOOD_SIDECAR, "contract": "backlog-promote.v0"},
            "no task_class": {k: v for k, v in _GOOD_SIDECAR.items() if k != "task_class"},
            "empty requires": {**_GOOD_SIDECAR, "requires": []},
            "traversal requires": {**_GOOD_SIDECAR, "requires": ["../x.md"]},
            "bad max_age": {**_GOOD_SIDECAR, "max_age_s": -1},
            "no promoted_by": {k: v for k, v in _GOOD_SIDECAR.items() if k != "promoted_by"},
            "no promoted_at": {k: v for k, v in _GOOD_SIDECAR.items() if k != "promoted_at"},
            "empty builders": {**_GOOD_SIDECAR, "builders": []},
        }
        for label, sidecar in bad_sidecars.items():
            with self.subTest(sidecar=label):
                sub = Path(self.enterContext(tempfile.TemporaryDirectory()))
                _write_json(sub / "refine-x-11111111.json", _refine_doc("refine-x-11111111"))
                _write_json(sub / f"refine-x-11111111{sources.PROMOTE_SUFFIX}", sidecar)
                scan = sources.refined_source(sub)
                self.assertEqual(len(scan), 0, f"{label} must not yield")
                self.assertIn("invalid promote sidecar", scan.rejected[0]["reason"])

    def test_half_written_sidecar_does_not_yield_and_is_reported(self) -> None:
        # Fault injection: the atomic write crashed between the temp file and the
        # replace, so only <intent>.promote.json.tmp-... exists.
        self._intent("refine-alpha-11111111")
        _write_json(self.refine / f"refine-alpha-11111111{sources.PROMOTE_SUFFIX}.tmp-1234-abcd",
                    _GOOD_SIDECAR)
        scan = sources.refined_source(self.refine)
        self.assertEqual(len(scan), 0)
        self.assertEqual(scan.rejected[0]["reason"], "not-promoted")

    def test_corrupt_sidecar_json_is_reported_not_raised(self) -> None:
        self._intent("refine-alpha-11111111")
        _write(self.refine / f"refine-alpha-11111111{sources.PROMOTE_SUFFIX}", "{not json")
        scan = sources.refined_source(self.refine)
        self.assertEqual(len(scan), 0)
        self.assertIn("invalid promote sidecar", scan.rejected[0]["reason"])

    def test_refine_result_without_final_text_is_reported(self) -> None:
        _write_json(self.refine / "refine-x-11111111.json",
                    {**_refine_doc("refine-x-11111111"), "final": None})
        _write_json(self.refine / f"refine-x-11111111{sources.PROMOTE_SUFFIX}", _GOOD_SIDECAR)
        scan = sources.refined_source(self.refine)
        self.assertEqual(len(scan), 0)
        self.assertIn("no 'final' text", scan.rejected[0]["reason"])

    def test_absent_directory_is_an_empty_scan_and_creates_nothing(self) -> None:
        missing = self.tmp / "nope"
        self.assertEqual(len(sources.refined_source(missing)), 0)
        self.assertFalse(missing.exists())


class PromoteRefineTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.refine = self.tmp / "refine"
        self.refine.mkdir(parents=True)
        self.intent_id = "refine-alpha-11111111"
        _write_json(self.refine / f"{self.intent_id}.json", _refine_doc(self.intent_id))
        self.sidecar_path = self.refine / f"{self.intent_id}{sources.PROMOTE_SUFFIX}"

    def _promote(self, **overrides):
        kwargs = dict(refine_dir=self.refine, task_class="build",
                      requires=["docs/plan.md"], max_age_s=3600,
                      promoted_by="derek", now="2026-09-06T10:00:00Z")
        kwargs.update(overrides)
        return sources.promote_refine(self.intent_id, **kwargs)

    def test_writes_the_sidecar(self) -> None:
        result = self._promote()
        self.assertEqual(result["status"], "written")
        doc = json.loads(self.sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(doc["contract"], sources.PROMOTE_CONTRACT)
        self.assertEqual(doc["requires"], ["docs/plan.md"])
        self.assertEqual(doc["promoted_at"], "2026-09-06T10:00:00Z")

    def test_identical_rerun_is_byte_identical_and_reports_unchanged(self) -> None:
        self._promote()
        before = self.sidecar_path.read_bytes()
        before_mtime = self.sidecar_path.stat().st_mtime_ns
        # A LATER clock: an idempotent re-promote must not let the timestamp
        # drift a no-op into a rewrite.
        result = self._promote(now="2026-09-06T23:59:59Z")
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(self.sidecar_path.read_bytes(), before)
        self.assertEqual(self.sidecar_path.stat().st_mtime_ns, before_mtime,
                         "an unchanged re-promote must not touch the file at all")

    def test_conflicting_rerun_is_refused(self) -> None:
        self._promote()
        before = self.sidecar_path.read_bytes()
        with self.assertRaises(ValueError) as ctx:
            self._promote(task_class="research")
        self.assertIn("--replace", str(ctx.exception))
        self.assertEqual(self.sidecar_path.read_bytes(), before)

    def test_replace_overwrites(self) -> None:
        self._promote()
        result = self._promote(task_class="research", now="2026-09-07T00:00:00Z",
                               replace=True)
        self.assertEqual(result["status"], "replaced")
        doc = json.loads(self.sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(doc["task_class"], "research")
        self.assertEqual(doc["promoted_at"], "2026-09-07T00:00:00Z")

    def test_unknown_intent_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            sources.promote_refine("refine-nope-99999999", refine_dir=self.refine,
                                   task_class="build", requires=["a.md"])
        self.assertIn("no refine result found", str(ctx.exception))

    def test_intent_id_traversal_is_refused(self) -> None:
        for bad in ("../evil", "a/b", ".."):
            with self.subTest(intent_id=bad), self.assertRaises(ValueError):
                sources.promote_refine(bad, refine_dir=self.refine,
                                       task_class="build", requires=["a.md"])

    def test_invalid_arguments_are_refused_before_any_write(self) -> None:
        with self.assertRaises(ValueError):
            self._promote(requires=["../escape.md"])
        self.assertFalse(self.sidecar_path.exists())

    def test_a_failed_replace_leaves_no_temp_litter(self) -> None:
        def boom(src, dst):
            raise OSError(5, "simulated device failure")

        with self.assertRaises(OSError):
            self._promote(replace_fn=boom)
        self.assertEqual(list(self.refine.glob("*.tmp-*")), [])
        self.assertFalse(self.sidecar_path.exists())

    def test_the_written_sidecar_round_trips_into_a_brief(self) -> None:
        self._promote()
        brief = sources.refined_source(self.refine).briefs[0]
        self.assertEqual(brief.source_ref, self.intent_id)
        self.assertEqual(brief.task_class, "build")


_WORTH = {
    "contract_version": "candidate-worth.v1",
    "entries": [
        {"candidate_id": "aaa_low", "worth_points": 2, "reason": "r", "author": "derek"},
        {"candidate_id": "bbb_high", "worth_points": 10, "reason": "r", "author": "derek"},
        {"candidate_id": "ccc_mid", "worth_points": 5, "reason": "r", "author": "derek"},
        {"candidate_id": "ddd_mid", "worth_points": 5, "reason": "r", "author": "derek"},
    ],
}


class CandidateSourceTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.worth = _write_json(self.tmp / "worth.json", _WORTH)
        self.results = _write_json(self.tmp / "results.json",
                                   {"contract_version": "experiment-results.v1",
                                    "results": []})

    def test_deterministic_order_worth_desc_then_candidate_id(self) -> None:
        order = [b.source_ref for b in sources.candidate_source(self.worth, self.results)]
        self.assertEqual(order, ["bbb_high", "ccc_mid", "ddd_mid", "aaa_low"])

    def test_a_results_row_carrying_experiment_id_skips_that_candidate(self) -> None:
        """THE BUG FIX, stated as the failing comparison it replaces.

        experiment-result.v1 rows carry ``experiment_id`` and have no
        ``candidate_id`` key at all. The drain compared candidate_id to
        candidate_id, so this row skipped nothing and a finished candidate could
        be dispatched again on every tick, forever. The second assertion pins the
        OLD expression so the fix cannot be quietly reverted.
        """
        rows = [{"contract_version": "experiment-result.v1",
                 "experiment_id": "bbb_high", "verdict": "supported"}]
        _write_json(self.results, {"results": rows})
        order = [b.source_ref for b in sources.candidate_source(self.worth, self.results)]
        self.assertNotIn("bbb_high", order)
        self.assertEqual(order[0], "ccc_mid")
        old_already_run = {r.get("candidate_id") for r in rows}
        self.assertNotIn("bbb_high", old_already_run,
                         "the old comparison could never have skipped this row")

    def test_derived_from_candidate_is_honoured_too(self) -> None:
        _write_json(self.results, {"results": [
            {"experiment_id": "exp-9999", "derived_from_candidate": "bbb_high"}]})
        order = [b.source_ref for b in sources.candidate_source(self.worth, self.results)]
        self.assertNotIn("bbb_high", order)

    def test_candidate_id_rows_still_skip_for_backward_compatibility(self) -> None:
        _write_json(self.results, {"results": [{"candidate_id": "bbb_high"}]})
        order = [b.source_ref for b in sources.candidate_source(self.worth, self.results)]
        self.assertNotIn("bbb_high", order)

    def test_all_run_yields_nothing(self) -> None:
        _write_json(self.results, {"results": [
            {"experiment_id": e["candidate_id"]} for e in _WORTH["entries"]]})
        self.assertEqual(len(sources.candidate_source(self.worth, self.results)), 0)

    def test_missing_files_yield_nothing_and_create_nothing(self) -> None:
        missing_worth = self.tmp / "nope-worth.json"
        missing_results = self.tmp / "nope-results.json"
        self.assertEqual(len(sources.candidate_source(missing_worth, missing_results)), 0)
        self.assertFalse(missing_worth.exists())
        self.assertFalse(missing_results.exists())

    def test_body_is_byte_identical_to_the_drains_prior_prompt(self) -> None:
        """B-04 keeps dispatching this text; moving the selection must not
        change one byte of it."""
        entry = {"candidate_id": "bbb_high", "worth_points": 10, "reason": "r"}
        prior = (
            f"Idle-drain dispatch (Banked Fire P5). Run experiment candidate "
            f"{entry['candidate_id']!r} (worth_points={entry.get('worth_points')}): "
            f"{entry.get('reason', '')}\n\n"
            f"This is unattended, gated, opportunistic fleet work — treat it as a normal build."
        )
        self.assertEqual(sources.candidate_brief(entry).body, prior)
        self.assertEqual(sources.candidate_source(self.worth, self.results).briefs[0].body,
                         prior)

    def test_brief_carries_the_proofing_class_and_a_derived_requires_glob(self) -> None:
        brief = sources.candidate_source(self.worth, self.results).briefs[0]
        self.assertEqual(brief.task_class, sources.CANDIDATE_TASK_CLASS)
        self.assertEqual(brief.task_class, "proofing")
        self.assertEqual(brief.requires, ("proposals/bbb_high.md",))
        self.assertEqual(brief.source, "candidate")

    def test_a_real_shaped_candidate_id_slugs_into_a_safe_requires_glob(self) -> None:
        # The live worth table's ids carry ':' and '+', which are not legal in a
        # filename on Windows and would be a second path segment on POSIX.
        entry = {"candidate_id": "backend_comparison:qwen2.5:14b:ollama-cuda+vulkan",
                 "worth_points": 6, "reason": "r"}
        brief = sources.candidate_brief(entry)
        self.assertEqual(brief.requires,
                         ("proposals/backend_comparison-qwen2.5-14b-ollama-cuda-vulkan.md",))
        self.assertRegex(brief.slug, r"^[A-Za-z0-9._-]+$")
        self.assertEqual(brief.source_ref, entry["candidate_id"],
                         "source_ref keeps the id VERBATIM; only the slug is sanitised")

    def test_select_candidate_wrapper_returns_the_raw_worth_entry(self) -> None:
        entry = sources.select_candidate(self.worth, self.results)
        self.assertEqual(entry["candidate_id"], "bbb_high")
        self.assertEqual(entry["worth_points"], 10)

    def test_already_run_ids_honours_exactly_the_documented_fields(self) -> None:
        self.assertEqual(sources.RESULT_CANDIDATE_FIELDS,
                         ("candidate_id", "experiment_id", "derived_from_candidate"))
        rows = [{"candidate_id": "a"}, {"experiment_id": "b"},
                {"derived_from_candidate": "c"}, {"run_id": "not-an-id"}, "junk"]
        self.assertEqual(sources.already_run_ids(rows), {"a", "b", "c"})
