"""HEARTH's expectation sidecar: what submit_task asked for, remembered across a
restart, and never louder than the run's own truth.

The invariant these tests defend beyond the obvious round-trip: this file is a
CACHE OF AN INTENTION, not a source of run state. It must fail soft (a corrupt
file is `{}` plus a visible warning, never an exception and never a silent
success), write atomically (a crash mid-write leaves the previous file intact),
and prune itself so it cannot grow without bound.
"""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface import task_expectations as te
from hearth.toolsurface.task_expectations import (ABSENT_GRACE_S, MAX_AGE_S_CAP,
                                                  STALE_ENTRY_MAX_AGE_S,
                                                  default_expectations_path,
                                                  hearth_var_root, load_expectations,
                                                  prune_expectations, record_expectation,
                                                  save_expectations, validate_max_age_s,
                                                  validate_requires)


def _iso(offset_s: float) -> str:
    from datetime import datetime, timedelta, timezone
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset_s)
    return dt.isoformat().replace("+00:00", "Z")


class SidecarPathTests(TestCase):
    def test_default_path_follows_hearth_root_env(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HEARTH_ROOT": tmp}):
                path = default_expectations_path()
            self.assertEqual(path, Path(tmp).resolve() / "var" / "task_lane" / "expectations.json")

    def test_default_path_without_env_is_under_the_repo_hearth_dir(self) -> None:
        env = dict(os.environ)
        env.pop("HEARTH_ROOT", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(hearth_var_root().name, "hearth")
            self.assertEqual(default_expectations_path().parts[-3:],
                             ("var", "task_lane", "expectations.json"))

    def test_the_env_is_read_at_call_time_not_import_time(self) -> None:
        # A test (or an operator) must be able to redirect the whole sidecar with
        # one env var; caching it at import would make that silently ineffective.
        with TemporaryDirectory() as a, TemporaryDirectory() as b:
            with patch.dict(os.environ, {"HEARTH_ROOT": a}):
                first = default_expectations_path()
            with patch.dict(os.environ, {"HEARTH_ROOT": b}):
                second = default_expectations_path()
        self.assertNotEqual(first, second)

    def test_reading_a_missing_sidecar_creates_no_directory(self) -> None:
        # The persistence invariant: patrol/masters_pet merely LOOKING must never
        # bring hearth/var into existence.
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HEARTH_ROOT": tmp}):
                doc = load_expectations()
            self.assertEqual(doc["expectations"], {})
            self.assertIsNone(doc["warning"])
            self.assertFalse((Path(tmp) / "var").exists())


class RecordAndLoadTests(TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "expectations.json"

    def test_record_then_load_round_trip(self) -> None:
        out = record_expectation("hearth-abc", {"max_age_s": 21600, "task_class": "build",
                                                "requires": ["docs/*.md"]}, path=self.path)
        self.assertEqual(out["plan_id"], "hearth-abc")
        self.assertEqual(out["count"], 1)
        doc = load_expectations(self.path)
        entry = doc["expectations"]["hearth-abc"]
        self.assertEqual(entry["max_age_s"], 21600)
        self.assertEqual(entry["task_class"], "build")
        self.assertEqual(entry["requires"], ["docs/*.md"])
        self.assertTrue(entry["submitted_at"].endswith("Z"))
        self.assertIsNone(doc["warning"])

    def test_restart_persistence_a_fresh_module_instance_reads_the_same(self) -> None:
        # "Restart" simulated honestly: a SECOND, independently loaded copy of the
        # module (its own globals, no shared in-memory state) reads the same file.
        record_expectation("hearth-restart", {"max_age_s": 3600}, path=self.path)
        spec = importlib.util.spec_from_file_location("te_fresh", te.__file__)
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        self.assertIsNot(fresh, te)
        doc = fresh.load_expectations(self.path)
        self.assertEqual(doc["expectations"]["hearth-restart"]["max_age_s"], 3600)

    def test_none_values_and_unknown_keys_are_not_stored(self) -> None:
        record_expectation("p", {"max_age_s": 60, "requires": None, "task_class": None,
                                 "secret": "nope"}, path=self.path)
        entry = load_expectations(self.path)["expectations"]["p"]
        self.assertEqual(set(entry), {"max_age_s", "submitted_at"})

    def test_recording_the_same_plan_id_twice_keeps_one_entry_last_write_wins(self) -> None:
        record_expectation("dup", {"max_age_s": 60}, path=self.path)
        record_expectation("dup", {"max_age_s": 7200}, path=self.path)
        doc = load_expectations(self.path)
        self.assertEqual(list(doc["expectations"]), ["dup"])
        self.assertEqual(doc["expectations"]["dup"]["max_age_s"], 7200)

    def test_recording_a_second_plan_id_keeps_the_first(self) -> None:
        record_expectation("a", {"max_age_s": 60}, path=self.path)
        record_expectation("b", {"max_age_s": 60}, path=self.path)
        self.assertEqual(sorted(load_expectations(self.path)["expectations"]), ["a", "b"])

    def test_blank_plan_id_and_non_dict_entry_rejected(self) -> None:
        for bad_id in ("", "   ", None, 5):
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(ValueError):
                    record_expectation(bad_id, {"max_age_s": 60}, path=self.path)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            record_expectation("p", ["not", "a", "dict"], path=self.path)  # type: ignore[arg-type]
        self.assertFalse(self.path.exists())

    def test_two_threads_recording_different_plan_ids_both_persist(self) -> None:
        # Read-modify-write under a lock. Without it the later reader/writer pair
        # clobbers the earlier one and entries vanish.
        n = 10
        barrier = threading.Barrier(n)
        errors: list[BaseException] = []

        def work(i: int) -> None:
            try:
                barrier.wait(timeout=10)
                record_expectation(f"hearth-{i}", {"max_age_s": 60 + i}, path=self.path)
            except BaseException as exc:  # surfaced, never swallowed
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(errors, [])
        doc = load_expectations(self.path)
        self.assertEqual(sorted(doc["expectations"]), sorted(f"hearth-{i}" for i in range(n)))


class FailureModeTests(TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "expectations.json"

    def test_missing_file_is_empty_with_no_warning(self) -> None:
        doc = load_expectations(self.path)
        self.assertEqual(doc["expectations"], {})
        self.assertIsNone(doc["warning"])

    def test_corrupt_json_is_empty_plus_a_warning_never_an_exception(self) -> None:
        self.path.write_text("{not json at all", encoding="utf-8")
        doc = load_expectations(self.path)
        self.assertEqual(doc["expectations"], {})
        self.assertIsNotNone(doc["warning"])
        self.assertIn("not valid JSON", doc["warning"])

    def test_non_dict_json_is_empty_plus_a_warning(self) -> None:
        self.path.write_text(json.dumps(["a", "list"]), encoding="utf-8")
        doc = load_expectations(self.path)
        self.assertEqual(doc["expectations"], {})
        self.assertIn("expected an object", doc["warning"])

    def test_malformed_entries_are_dropped_and_counted_in_the_warning(self) -> None:
        self.path.write_text(json.dumps({"good": {"max_age_s": 60}, "bad": 7}), encoding="utf-8")
        doc = load_expectations(self.path)
        self.assertEqual(list(doc["expectations"]), ["good"])
        self.assertIn("1 malformed entry", doc["warning"])

    def test_unreadable_path_is_a_warning_not_a_raise(self) -> None:
        # A directory where the file should be: OSError on read, reported.
        self.path.mkdir()
        doc = load_expectations(self.path)
        self.assertEqual(doc["expectations"], {})
        self.assertIn("unreadable", doc["warning"])

    def test_crash_between_temp_write_and_replace_leaves_the_previous_file_intact(self) -> None:
        record_expectation("first", {"max_age_s": 111}, path=self.path)
        before = self.path.read_bytes()

        def boom(src, dst):
            raise OSError("simulated crash between temp write and replace")

        with self.assertRaises(OSError):
            save_expectations({"second": {"max_age_s": 222}}, self.path, replace=boom)

        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(load_expectations(self.path)["expectations"]["first"]["max_age_s"], 111)
        # No half-written file at the final path, and no temp litter beside it.
        self.assertEqual(list(self.path.parent.glob(self.path.name + ".tmp-*")), [])

    def test_failed_record_leaves_the_previous_content_readable(self) -> None:
        record_expectation("first", {"max_age_s": 111}, path=self.path)

        def boom(src, dst):
            raise OSError("nope")

        with self.assertRaises(OSError):
            record_expectation("second", {"max_age_s": 222}, path=self.path, replace=boom)
        doc = load_expectations(self.path)
        self.assertEqual(list(doc["expectations"]), ["first"])

    def test_save_creates_parent_directories(self) -> None:
        nested = Path(self._tmp.name) / "var" / "task_lane" / "expectations.json"
        save_expectations({"p": {"max_age_s": 1}}, nested)
        self.assertTrue(nested.is_file())


class ValidateMaxAgeTests(TestCase):
    def test_valid_values_return_ints(self) -> None:
        self.assertEqual(validate_max_age_s(21600), 21600)
        self.assertEqual(validate_max_age_s(1), 1)
        self.assertEqual(validate_max_age_s(MAX_AGE_S_CAP), MAX_AGE_S_CAP)
        self.assertIsNone(validate_max_age_s(None))

    def test_invalid_values_raise(self) -> None:
        for bad in (0, -1, 12.5, 3600.0, "3600", True, False, [3600], {"s": 1},
                    MAX_AGE_S_CAP + 1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_max_age_s(bad)

    def test_the_cap_is_seven_days(self) -> None:
        self.assertEqual(MAX_AGE_S_CAP, 7 * 24 * 3600)

    def test_error_message_names_the_field(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_max_age_s(-1, where="manifest[3].max_age_s")
        self.assertIn("manifest[3].max_age_s", str(ctx.exception))


class ValidateRequiresTests(TestCase):
    def test_valid_relative_globs_pass_through_verbatim(self) -> None:
        value = ["docs/*.md", "src/**/*.py", "report.json"]
        self.assertEqual(validate_requires(value), value)
        self.assertIsNone(validate_requires(None))

    def test_absolute_paths_are_refused(self) -> None:
        for bad in ("/etc/passwd", "\\\\server\\share\\x", "/docs/a.md"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_requires([bad])

    def test_drive_letters_are_refused(self) -> None:
        for bad in ("C:\\work\\x.md", "c:/work/x.md"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_requires([bad])

    def test_parent_segments_are_refused_in_either_separator(self) -> None:
        for bad in ("../secrets.json", "docs/../../etc/x", "docs\\..\\..\\x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_requires([bad])

    def test_a_dotdot_inside_a_name_is_not_a_parent_segment(self) -> None:
        # "..foo" is a filename, not a traversal — the check is per SEGMENT.
        self.assertEqual(validate_requires(["docs/..foo.md"]), ["docs/..foo.md"])

    def test_empty_list_and_non_list_are_refused(self) -> None:
        for bad in ([], "docs/*.md", {}, 5):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_requires(bad)

    def test_non_string_and_blank_items_are_refused(self) -> None:
        for bad in ([1], [None], [""], ["   "], [["nested"]]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_requires(bad)

    def test_nul_and_newline_are_refused(self) -> None:
        for bad in ("a\x00b", "a\nb", "a\rb"):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError):
                    validate_requires([bad])

    def test_oversized_lists_and_items_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            validate_requires(["a"] * (te.MAX_REQUIRES_ITEMS + 1))
        with self.assertRaises(ValueError):
            validate_requires(["a" * (te.MAX_REQUIRES_ITEM_CHARS + 1)])

    def test_error_message_names_the_offending_index(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_requires(["ok.md", "/absolute"], where="manifest[2].requires")
        self.assertIn("manifest[2].requires[1]", str(ctx.exception))


class PruneTests(TestCase):
    def _entry(self, age_s: float, **over) -> dict:
        entry = {"max_age_s": 3600, "submitted_at": _iso(-age_s)}
        entry.update(over)
        return entry

    def test_finished_run_entry_is_pruned(self) -> None:
        exp = {"done": self._entry(60)}
        records = [{"plan_id": "done", "has_result": True}]
        kept, pruned = prune_expectations(exp, records)
        self.assertEqual(kept, {})
        self.assertEqual(pruned, [{"plan_id": "done", "reason": "finished"}])

    def test_live_unfinished_entry_is_kept(self) -> None:
        exp = {"live": self._entry(60)}
        records = [{"plan_id": "live", "has_result": False}]
        kept, pruned = prune_expectations(exp, records)
        self.assertEqual(list(kept), ["live"])
        self.assertEqual(pruned, [])

    def test_stale_entry_is_pruned_even_while_its_run_is_unfinished(self) -> None:
        exp = {"ancient": self._entry(STALE_ENTRY_MAX_AGE_S + 60)}
        records = [{"plan_id": "ancient", "has_result": False}]
        kept, pruned = prune_expectations(exp, records)
        self.assertEqual(kept, {})
        self.assertEqual(pruned[0]["reason"], "stale")

    def test_absent_run_is_kept_inside_the_grace_and_pruned_after_it(self) -> None:
        # An expectation recorded seconds ago describes a task the conductor has
        # not turned into a run dir yet — pruning it would lose the protection
        # before the run it protects exists.
        young = {"pending": self._entry(5)}
        kept, pruned = prune_expectations(young, [])
        self.assertEqual(list(kept), ["pending"])
        self.assertEqual(pruned, [])

        old = {"pending": self._entry(ABSENT_GRACE_S + 60)}
        kept, pruned = prune_expectations(old, [])
        self.assertEqual(kept, {})
        self.assertEqual(pruned[0]["reason"], "absent")

    def test_malformed_entry_is_pruned(self) -> None:
        kept, pruned = prune_expectations({"junk": "not a dict"}, [])
        self.assertEqual(kept, {})
        self.assertEqual(pruned[0]["reason"], "malformed")

    def test_unparseable_submitted_at_is_not_aged_out_blindly(self) -> None:
        # Age unknown => rules 2 and 3 cannot judge; rule 1 still can.
        exp = {"weird": {"max_age_s": 60, "submitted_at": "not-a-timestamp"}}
        kept, _ = prune_expectations(exp, [])
        self.assertEqual(list(kept), ["weird"])
        kept, pruned = prune_expectations(exp, [{"plan_id": "weird", "has_result": True}])
        self.assertEqual(kept, {})
        self.assertEqual(pruned[0]["reason"], "finished")

    def test_prune_does_not_mutate_its_inputs(self) -> None:
        exp = {"done": self._entry(60), "live": self._entry(60)}
        snapshot = json.loads(json.dumps(exp))
        records = [{"plan_id": "done", "has_result": True},
                   {"plan_id": "live", "has_result": False}]
        records_snapshot = json.loads(json.dumps(records))
        prune_expectations(exp, records)
        self.assertEqual(exp, snapshot)
        self.assertEqual(records, records_snapshot)

    def test_prune_is_idempotent(self) -> None:
        exp = {"done": self._entry(60), "live": self._entry(60)}
        records = [{"plan_id": "done", "has_result": True},
                   {"plan_id": "live", "has_result": False}]
        once, _ = prune_expectations(exp, records)
        twice, pruned_again = prune_expectations(once, records)
        self.assertEqual(once, twice)
        self.assertEqual(pruned_again, [])

    def test_now_is_injectable_so_the_rules_are_testable_without_sleeping(self) -> None:
        exp = {"p": {"max_age_s": 60, "submitted_at": _iso(0)}}
        kept, _ = prune_expectations(exp, [], now=time.time() + ABSENT_GRACE_S + 60)
        self.assertEqual(kept, {})
