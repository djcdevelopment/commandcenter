"""Stream-scoped acceptance gate tests.

Verifies that required-deliverable presence is checked BEFORE behavior ranking,
and that absent/empty `requires` is a strict no-op (ranking byte-identical to
today).  No live SSH or network: every `git ls-tree` call is intercepted via the
injectable `list_files_fn` parameter on each public function.

Pour-c2 shape (test_pour_c2_shape_*): one complete lap that ships both the
corpus_guard implementation and its tests, plus a "collector-only" lap that ships
the implementation but omits the test file.  Only the complete lap survives
acceptance; the collector-only lap is excluded.
"""

from __future__ import annotations

from unittest import TestCase

from tools.workflow.assay_acceptance import (
    check_lap_acceptance,
    filter_scoreboard_by_acceptance,
    parse_ccmeta_requires,
    rank_with_acceptance,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _entry(worker: str, behavior_score: int, plan: str = "pour-c2") -> dict:
    return {
        "worker": worker,
        "branch": f"ccfarm/{plan}/{worker}/lap1",
        "score": behavior_score,
        "behavior_score": behavior_score,
    }


def _files_fn(files_by_branch: dict):
    """Inject a branch→filelist map; raises KeyError on unknown branch."""
    def _fn(branch: str) -> list[str]:
        return list(files_by_branch[branch])
    return _fn


_REQUIRES_CORPUS = [
    "tools/workflow/corpus_guard.py",
    "tests/workflow/test_corpus_guard.py",
]

_COMPLETE_FILES = [
    "tools/workflow/corpus_guard.py",
    "tests/workflow/test_corpus_guard.py",
    "RETRO.md",
]

_COLLECTOR_ONLY_FILES = [
    "tools/workflow/corpus_guard.py",
    # missing: tests/workflow/test_corpus_guard.py
    "RETRO.md",
]


# ---------------------------------------------------------------------------
# parse_ccmeta_requires
# ---------------------------------------------------------------------------

class ParseCcmetaRequiresTests(TestCase):
    def test_no_ccmeta_returns_empty(self) -> None:
        self.assertEqual(parse_ccmeta_requires("Build a reference workflow."), [])

    def test_empty_requires_returns_empty(self) -> None:
        text = '<!-- CCMETA\n{"requires": []}\n-->\nBuild.'
        self.assertEqual(parse_ccmeta_requires(text), [])

    def test_null_requires_returns_empty(self) -> None:
        text = '<!-- CCMETA\n{"requires": null}\n-->\nBuild.'
        self.assertEqual(parse_ccmeta_requires(text), [])

    def test_absent_requires_key_returns_empty(self) -> None:
        text = '<!-- CCMETA\n{"builders": ["cc-builder-1"]}\n-->\nBuild.'
        self.assertEqual(parse_ccmeta_requires(text), [])

    def test_parses_exact_paths(self) -> None:
        text = (
            '<!-- CCMETA\n'
            '{"requires": ["tools/text/wordcount.py", "tests/workflow/test_wordcount.py"]}\n'
            '-->\nBuild.'
        )
        self.assertEqual(
            parse_ccmeta_requires(text),
            ["tools/text/wordcount.py", "tests/workflow/test_wordcount.py"],
        )

    def test_parses_glob_patterns(self) -> None:
        text = '<!-- CCMETA\n{"requires": ["tools/workflow/corpus_guard.py"]}\n-->\nBuild.'
        self.assertEqual(parse_ccmeta_requires(text), ["tools/workflow/corpus_guard.py"])


# ---------------------------------------------------------------------------
# check_lap_acceptance
# ---------------------------------------------------------------------------

class CheckLapAcceptanceTests(TestCase):
    _branch = "ccfarm/pour-c2/w1/lap1"

    def test_empty_requires_always_passes_without_calling_list_files(self) -> None:
        called = []
        def _bomb(branch: str) -> list[str]:
            called.append(branch)
            return []
        result = check_lap_acceptance(self._branch, [], list_files_fn=_bomb)
        self.assertTrue(result["passed"])
        self.assertEqual(result["missing_globs"], [])
        self.assertEqual(called, [], "list_files_fn must not be called when requires is empty")

    def test_all_globs_present_passes(self) -> None:
        files = ["tools/text/wordcount.py", "tests/workflow/test_wordcount.py", "README.md"]
        result = check_lap_acceptance(
            self._branch,
            ["tools/text/wordcount.py", "tests/workflow/test_wordcount.py"],
            list_files_fn=lambda _: files,
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["missing_globs"], [])

    def test_missing_one_glob_fails_and_names_it(self) -> None:
        files = ["tools/text/wordcount.py", "README.md"]
        result = check_lap_acceptance(
            self._branch,
            ["tools/text/wordcount.py", "tests/workflow/test_wordcount.py"],
            list_files_fn=lambda _: files,
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_globs"], ["tests/workflow/test_wordcount.py"])

    def test_glob_wildcard_matches_with_fnmatch(self) -> None:
        files = ["tests/workflow/test_wordcount.py"]
        result = check_lap_acceptance(
            self._branch,
            ["tests/workflow/test_*.py"],
            list_files_fn=lambda _: files,
        )
        self.assertTrue(result["passed"])

    def test_list_files_fn_receives_the_branch_name(self) -> None:
        seen = []
        def _capture(branch: str) -> list[str]:
            seen.append(branch)
            return ["tools/workflow/corpus_guard.py"]
        check_lap_acceptance(self._branch, ["tools/workflow/corpus_guard.py"],
                             list_files_fn=_capture)
        self.assertEqual(seen, [self._branch])


# ---------------------------------------------------------------------------
# filter_scoreboard_by_acceptance
# ---------------------------------------------------------------------------

class FilterScoreboardByAcceptanceTests(TestCase):
    def test_empty_requires_returns_scoreboard_unchanged_noop(self) -> None:
        board = [_entry("w1", 70), _entry("w2", 69)]
        accepted, rejected = filter_scoreboard_by_acceptance(board, [])
        # byte-identical: same objects, no copies, no mutations
        self.assertIs(accepted[0], board[0])
        self.assertIs(accepted[1], board[1])
        self.assertEqual(rejected, [])

    def test_all_deliverables_present_all_accepted(self) -> None:
        board = [_entry("w1", 70)]
        files = {"ccfarm/pour-c2/w1/lap1": _COMPLETE_FILES}
        accepted, rejected = filter_scoreboard_by_acceptance(
            board, _REQUIRES_CORPUS, list_files_fn=_files_fn(files)
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["worker"], "w1")
        self.assertEqual(rejected, [])

    def test_missing_deliverable_moves_lap_to_rejected(self) -> None:
        board = [_entry("w1", 70)]
        files = {"ccfarm/pour-c2/w1/lap1": _COLLECTOR_ONLY_FILES}
        accepted, rejected = filter_scoreboard_by_acceptance(
            board, _REQUIRES_CORPUS, list_files_fn=_files_fn(files)
        )
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 1)
        self.assertTrue(rejected[0]["acceptance_failed"])
        self.assertIn("tests/workflow/test_corpus_guard.py", rejected[0]["missing_globs"])

    def test_accepted_entries_are_not_mutated(self) -> None:
        board = [_entry("w1", 70)]
        files = {"ccfarm/pour-c2/w1/lap1": _COMPLETE_FILES}
        accepted, _ = filter_scoreboard_by_acceptance(
            board, _REQUIRES_CORPUS, list_files_fn=_files_fn(files)
        )
        self.assertNotIn("acceptance_failed", accepted[0])
        self.assertNotIn("missing_globs", accepted[0])


# ---------------------------------------------------------------------------
# rank_with_acceptance — core scenarios
# ---------------------------------------------------------------------------

class RankWithAcceptanceTests(TestCase):
    def test_all_deliverables_present_selects_winner(self) -> None:
        board = [_entry("cc-builder-1", 70)]
        files = {"ccfarm/pour-c2/cc-builder-1/lap1": _COMPLETE_FILES}
        result = rank_with_acceptance(board, _REQUIRES_CORPUS, list_files_fn=_files_fn(files))
        self.assertEqual(result["outcome"], "winner_selected")
        self.assertEqual(result["winner"], "cc-builder-1")
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(result["rejected"], [])

    def test_missing_deliverable_excluded_no_winner_when_only_lap(self) -> None:
        board = [_entry("cc-builder-1", 80)]
        files = {"ccfarm/pour-c2/cc-builder-1/lap1": _COLLECTOR_ONLY_FILES}
        result = rank_with_acceptance(board, _REQUIRES_CORPUS, list_files_fn=_files_fn(files))
        self.assertEqual(result["outcome"], "no_winner")
        self.assertIsNone(result["winner"])
        self.assertIn("needs curation", result["reason"])
        self.assertEqual(len(result["rejected"]), 1)
        self.assertTrue(result["rejected"][0]["acceptance_failed"])

    def test_empty_requires_noop_ranking_identical_to_plain_sort(self) -> None:
        board = [_entry("cc-builder-1", 70), _entry("cc-builder-2", 69)]
        result = rank_with_acceptance(board, [], list_files_fn=lambda _: [])
        self.assertEqual(result["outcome"], "winner_selected")
        self.assertEqual(result["winner"], "cc-builder-1")
        self.assertEqual(len(result["accepted"]), 2)
        self.assertEqual(result["rejected"], [])

    def test_all_laps_fail_acceptance_surfaces_no_winner(self) -> None:
        board = [_entry("cc-builder-1", 80), _entry("cc-builder-2", 70)]
        files = {
            "ccfarm/pour-c2/cc-builder-1/lap1": ["README.md"],
            "ccfarm/pour-c2/cc-builder-2/lap1": ["README.md"],
        }
        result = rank_with_acceptance(board, _REQUIRES_CORPUS, list_files_fn=_files_fn(files))
        self.assertEqual(result["outcome"], "no_winner")
        self.assertIsNone(result["winner"])
        self.assertEqual(len(result["rejected"]), 2)
        self.assertEqual(result["accepted"], [])
        # confirm it's an explicit needs-curation signal, not a silent crowning
        self.assertIn("needs curation", result["reason"])

    # -----------------------------------------------------------------------
    # Pour-c2 shape
    # -----------------------------------------------------------------------

    def test_pour_c2_shape_complete_lap_wins_over_collector_only(self) -> None:
        """Complete lap (code + tests) beats collector-only lap (code, no tests).

        cc-builder-1 has both deliverables; cc-builder-2 has the implementation
        but is missing the test file.  cc-builder-2 is excluded before ranking,
        so cc-builder-1 wins even though its behavior_score is only 1 point higher.
        """
        board = [
            _entry("cc-builder-1", 70),  # complete: has both
            _entry("cc-builder-2", 69),  # collector-only: missing test
        ]
        files = {
            "ccfarm/pour-c2/cc-builder-1/lap1": _COMPLETE_FILES,
            "ccfarm/pour-c2/cc-builder-2/lap1": _COLLECTOR_ONLY_FILES,
        }
        result = rank_with_acceptance(board, _REQUIRES_CORPUS, list_files_fn=_files_fn(files))
        self.assertEqual(result["outcome"], "winner_selected")
        self.assertEqual(result["winner"], "cc-builder-1")
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(len(result["rejected"]), 1)
        self.assertTrue(result["rejected"][0]["acceptance_failed"])
        self.assertEqual(result["rejected"][0]["worker"], "cc-builder-2")
        self.assertIn("tests/workflow/test_corpus_guard.py",
                      result["rejected"][0]["missing_globs"])

    def test_pour_c2_shape_higher_scoring_incomplete_lap_is_excluded(self) -> None:
        """Even if the collector-only lap has a higher score, it is excluded."""
        board = [
            _entry("cc-builder-1", 70),  # complete
            _entry("cc-builder-2", 85),  # higher score but missing tests
        ]
        files = {
            "ccfarm/pour-c2/cc-builder-1/lap1": _COMPLETE_FILES,
            "ccfarm/pour-c2/cc-builder-2/lap1": _COLLECTOR_ONLY_FILES,
        }
        result = rank_with_acceptance(board, _REQUIRES_CORPUS, list_files_fn=_files_fn(files))
        self.assertEqual(result["outcome"], "winner_selected")
        # the 85-point lap is excluded; the 70-point complete lap wins
        self.assertEqual(result["winner"], "cc-builder-1")
        self.assertEqual(result["rejected"][0]["worker"], "cc-builder-2")
