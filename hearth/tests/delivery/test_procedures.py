"""procedures.choose and load: the door's choice of a delivery procedure from delivery-procedures.v1 (ADR-0061).

Tables here are small and hand-written; the one test on the tracked table asserts only that it loads and answers,
because its counts change with every verdict the registry exports."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hearth.delivery import procedures

TRACKED = Path(__file__).resolve().parents[3] / "host" / "omen-linux" / "hearth-production" / "delivery-procedures-linux.json"


def counts(accepted: int, rejected: int, accepted_briefs: int, briefs: int | None = None) -> dict:
    return {"accepted": accepted, "rejected": rejected, "briefs": briefs if briefs is not None else accepted_briefs,
            "accepted_briefs": accepted_briefs}


def table(backends: dict, minimum: int = 2) -> dict:
    return {"schema": procedures.SCHEMA, "rule": {"min_accepted_briefs": minimum}, "records": 1, "backends": backends}


class ChooseTests(unittest.TestCase):
    def test_profile_counts_do_not_borrow_backend_or_another_profile_counts(self) -> None:
        t = table({"b": {"all": {"carry": counts(20, 0, 10)}, "profiles": {
            "current": {"all": {"one_call": counts(3, 0, 2)}},
            "old": {"all": {"carry": counts(10, 0, 5)}}}}})
        self.assertEqual(procedures.choose(t, "b", None, serving_profile_sha256="current")[0], "one_call")
        for backend, profile in (("b", "absent"), ("new-backend", "current")):
            self.assertEqual(procedures.choose(t, backend, None, serving_profile_sha256=profile,
                                              require_profile=True)[1]["level"], "none")
        legacy = table({"b": {"all": {"carry": counts(20, 0, 10)}}})
        self.assertEqual(procedures.choose(legacy, "b", None)[0], "carry")
        self.assertEqual(procedures.choose(legacy, "b", None, serving_profile_sha256="current",
                                          require_profile=True)[1]["level"], "none")

    def test_family_level_decides_before_the_backend_level(self) -> None:
        """evidence/wave8/RESULT.md: backoff on omen-dense-27b, `code_review`: carry (6 accepted, 1 rejected, 2 briefs with
        an acceptance) against one call (8, 22, 1 brief): the door chose carry at the family level. Here the backend
        level says the opposite, so only the family level can have decided."""
        t = table({"b": {"families": {"code_review": {"carry": counts(6, 1, 2), "one_call": counts(8, 22, 1)}},
                         "all": {"carry": counts(0, 9, 0), "one_call": counts(30, 0, 5)}}})
        picked, basis = procedures.choose(t, "b", "code_review")
        self.assertEqual((picked, basis["level"]), ("carry", "family"))
        self.assertEqual(basis["counts"]["carry"], counts(6, 1, 2))
        self.assertEqual(basis["rule"], {"min_accepted_briefs": 2})

    def test_backend_level_answers_when_the_family_has_no_qualifying_procedure(self) -> None:
        """evidence/wave8/RESULT.md: perception chose carry at the backend level (the family has one brief with an
        acceptance, below the rule's 2; overall carry 7 and 3 over 4 briefs, one call 11 and 32). A family with no entry
        at all falls to the same level."""
        t = table({"b": {"families": {"code_review": {"carry": counts(5, 0, 1)}},
                         "all": {"carry": counts(7, 3, 3, 4), "one_call": counts(11, 32, 1, 5)}}})
        for family in ("code_review", "summarization", None):
            picked, basis = procedures.choose(t, "b", family)
            self.assertEqual((picked, basis["level"]), ("carry", "backend"), family)
        self.assertEqual(procedures.choose(t, "b", "code_review")[1]["counts"]["carry"]["accepted"], 7)

    def test_nothing_qualifying_is_one_call_at_level_none(self) -> None:
        """docs/delivery.md "How the door chooses the procedure": no table, no entry for the backend, or nothing
        qualifying gives one_call at level none (evidence/rehearsal/RESULT.md: the fast seat and tool seats have no
        accepted delivery, so their works run one call)."""
        below = table({"b": {"families": {}, "all": {"carry": counts(9, 0, 1), "one_call": counts(0, 4, 0)}}})
        for tab, backend in ((None, "b"), (below, "other"), (below, "b"),
                             (table({"b": {"families": {"f": {}}, "all": {}}}), "b")):
            picked, basis = procedures.choose(tab, backend, "f")
            self.assertEqual((picked, basis["level"], basis["counts"]), ("one_call", "none", {}), (tab, backend))
        self.assertIsNone(procedures.choose(None, "b", "f")[1]["rule"])
        self.assertEqual(procedures.choose(below, "b", "f")[1]["rule"], {"min_accepted_briefs": 2})

    def test_a_raised_minimum_disqualifies_what_it_admitted(self) -> None:
        """docs/delivery.md: a procedure qualifies at a level with at least rule.min_accepted_briefs distinct accepted
        briefs (2 today); the number is the table's, not the code's."""
        backends = {"b": {"families": {}, "all": {"carry": counts(6, 1, 2)}}}
        self.assertEqual(procedures.choose(table(backends, 2), "b", None)[0], "carry")
        picked, basis = procedures.choose(table(backends, 3), "b", None)
        self.assertEqual((picked, basis["level"]), ("one_call", "none"))

    def test_higher_accepted_share_wins_then_more_accepted_then_one_call(self) -> None:
        """evidence/rehearsal/RESULT.md, work_a745c074: carry at family level, carry 7 accepted and 3 rejected against
        one call 8 and 22: both qualify, the share decides (0.70 against 0.27), not the count of acceptances."""
        both = {"carry": counts(7, 3, 2), "one_call": counts(8, 22, 2)}
        self.assertEqual(procedures.choose(table({"b": {"all": both}}), "b", None)[0], "carry")
        flipped = {"carry": counts(7, 22, 2), "one_call": counts(2, 1, 2)}
        self.assertEqual(procedures.choose(table({"b": {"all": flipped}}), "b", None)[0], "one_call")
        more = {"carry": counts(6, 2, 2), "one_call": counts(3, 1, 2)}   # equal share 0.75: the larger count
        self.assertEqual(procedures.choose(table({"b": {"all": more}}), "b", None)[0], "carry")

    def test_an_exact_tie_goes_to_one_call(self) -> None:
        """docs/delivery.md: among qualifying procedures the door takes the higher accepted share, then more accepted
        verdicts, then one_call; ADR-0061: ties do not move a backend onto the thinking turn."""
        tie = {"carry": counts(4, 4, 2), "one_call": counts(4, 4, 2)}
        for order in (tie, dict(reversed(list(tie.items())))):
            picked, basis = procedures.choose(table({"b": {"all": order}}), "b", None)
            self.assertEqual((picked, basis["level"]), ("one_call", "backend"))

    def test_a_name_the_door_does_not_run_is_ignored_and_reported(self) -> None:
        """ADR-0061 (the table is generated from the registry): a procedure name the door has no code for never wins and is
        listed in basis["ignored"]."""
        t = table({"b": {"all": {"carry": counts(2, 0, 2), "items": counts(50, 0, 40)}}})
        picked, basis = procedures.choose(t, "b", None)
        self.assertEqual((picked, basis["ignored"]), ("carry", ["items"]))
        self.assertNotIn("items", basis["counts"])


class MalformedTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, content: object) -> Path:
        path = self.root / "t.json"
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return path

    def test_a_table_file_that_exists_but_is_not_a_table_is_refused_by_name(self) -> None:
        """docs/delivery.md: "A table file that exists but is not a valid table refuses the submit" (a silent
        one_call would hide a broken deploy)."""
        good = table({})
        bad = ("{nope", [], {**good, "schema": "delivery-procedures.v0"}, {**good, "backends": []},
               {k: v for k, v in good.items() if k != "rule"}, {**good, "rule": {"min_accepted_briefs": "2"}},
               {**good, "rule": {"min_accepted_briefs": True}}, {**good, "rule": {}})
        for content in bad:
            with self.assertRaises(procedures.ProcedureTableError, msg=repr(content)):
                procedures.load(self.write(content))

    def test_load_returns_the_sha256_of_the_bytes_and_no_file_is_no_table(self) -> None:
        """docs/delivery.md: the manifest records `table_sha256`; an absent file or an unset variable is no table."""
        import hashlib
        path = self.write(table({}))
        loaded, digest = procedures.load(path)
        self.assertEqual((loaded["schema"], digest), (procedures.SCHEMA, hashlib.sha256(path.read_bytes()).hexdigest()))
        self.assertEqual(procedures.load(self.root / "absent.json"), (None, None))
        self.assertEqual(procedures.load(None), (None, None))
        self.assertEqual(procedures.load(""), (None, None))

    def test_an_entry_or_count_that_is_malformed_refuses_choose(self) -> None:
        """ADR-0061: a level whose counts are not integers, or a backend entry that is not an object, is refused when
        the door reads it; it is not skipped."""
        cases = (
            {"b": "x"},
            {"b": {"families": [1]}},
            {"b": {"all": [1]}},
            {"b": {"all": {"carry": {"accepted": 1}}}},
            {"b": {"all": {"carry": {**counts(1, 0, 2), "accepted": "1"}}}},
            {"b": {"all": {"carry": {**counts(1, 0, 2), "accepted_briefs": True}}}},
            {"b": {"all": {"carry": []}}},
        )
        for backends in cases:
            with self.assertRaises(procedures.ProcedureTableError, msg=repr(backends)):
                procedures.choose(table(backends), "b", None)
        with self.assertRaises(procedures.ProcedureTableError):   # a malformed family level, asked for by name
            procedures.choose(table({"b": {"families": {"f": {"carry": {"accepted": 1}}}}}), "b", "f")

    def test_a_malformed_entry_of_another_backend_does_not_refuse_this_one(self) -> None:
        """ADR-0061 (the lane choice asks about two backends): only the backend asked about is read."""
        t = table({"good": {"all": {"carry": counts(2, 0, 2)}}, "bad": "x"})
        self.assertEqual(procedures.choose(t, "good", None)[0], "carry")


class TrackedTableTests(unittest.TestCase):
    def test_the_tracked_table_loads_and_the_deep_seat_gets_a_known_procedure(self) -> None:
        """evidence/wave8/RESULT.md and rehearsal/RESULT.md: the door chose from this file's verdicts. No count is pinned
        here: they change as verdicts come in."""
        loaded, digest = procedures.load(TRACKED)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(digest), 64)
        picked, basis = procedures.choose(loaded, "omen-dense-27b", "code_review")
        self.assertIn(picked, procedures.KNOWN)
        self.assertIn(basis["level"], ("family", "backend", "none"))
        self.assertIn(procedures.choose(loaded, "omen-dense-27b", None)[0], procedures.KNOWN)


if __name__ == "__main__":
    unittest.main()
