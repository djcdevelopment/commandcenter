"""select_next: the priority rule, the closed scope enum, purity, laziness."""
from __future__ import annotations

from unittest import TestCase

from hearth.backlog.briefs import Brief
from hearth.backlog.select import PRIORITY, SCOPES, select_next


def _brief(source: str, ref: str, slug: str | None = None) -> Brief:
    return Brief(slug=slug or ref.replace(":", "-"), title=f"{source} {ref}",
                 body=f"body for {ref}", builders=None, task_class=None,
                 est_tokens=None, requires=(), max_age_s=None,
                 source=source, source_ref=ref)


def _all_sources() -> dict:
    return {
        "authored": [_brief("authored", "a1"), _brief("authored", "a2")],
        "refined": [_brief("refined", "r1"), _brief("refined", "r2")],
        "candidate": [_brief("candidate", "c1"), _brief("candidate", "c2")],
    }


class PriorityTests(TestCase):
    def test_authored_beats_refined_beats_candidate(self) -> None:
        sources = _all_sources()
        self.assertEqual(select_next("all", sources).source_ref, "a1")
        del sources["authored"]
        self.assertEqual(select_next("all", sources).source_ref, "r1")
        del sources["refined"]
        self.assertEqual(select_next("all", sources).source_ref, "c1")

    def test_authored_beats_a_candidate_with_the_highest_worth(self) -> None:
        """Worth points price an experiment's value to the belief corpus. They
        are not a bid against a human's stated intent, so no worth score may
        promote a candidate over an authored brief."""
        # The candidate source hands its list highest-worth-first, so c1 here IS
        # the top-priced candidate in the table.
        sources = {"authored": [_brief("authored", "a1")],
                   "candidate": [_brief("candidate", "top_worth_10"),
                                 _brief("candidate", "next_worth_9")]}
        self.assertEqual(select_next("all", sources).source_ref, "a1")

    def test_priority_tuple_is_the_rule(self) -> None:
        self.assertEqual(PRIORITY, ("authored", "refined", "candidate"))

    def test_the_first_brief_of_the_winning_source_is_chosen(self) -> None:
        # Ordering WITHIN a source belongs to the source generator (oldest mtime
        # / oldest promotion / highest worth); select_next must preserve the
        # order it is handed, not re-sort it.
        sources = {"authored": [_brief("authored", "second"), _brief("authored", "first")]}
        self.assertEqual(select_next("all", sources).source_ref, "second")


class ScopeTests(TestCase):
    def test_every_scope_value_admits_exactly_its_sources(self) -> None:
        expected = {
            "all": "a1",
            "authored": "a1",
            "refined": "r1",
            "candidate": "c1",
            "authored+refined": "a1",
        }
        self.assertEqual(set(expected), set(SCOPES))
        for scope, first in expected.items():
            with self.subTest(scope=scope):
                self.assertEqual(select_next(scope, _all_sources()).source_ref, first)

    def test_a_narrow_scope_never_reaches_a_source_it_excludes(self) -> None:
        sources = _all_sources()
        self.assertEqual(select_next("candidate", sources).source_ref, "c1")
        self.assertEqual(select_next("refined", sources).source_ref, "r1")
        del sources["authored"]
        self.assertEqual(select_next("authored+refined", sources).source_ref, "r1")
        del sources["refined"]
        self.assertIsNone(select_next("authored+refined", sources),
                          "authored+refined must never fall through to candidate")

    def test_unknown_scope_raises(self) -> None:
        for bad in ("everything", "", "ALL", None, 3, "authored+candidate"):
            with self.subTest(scope=bad), self.assertRaises(ValueError):
                select_next(bad, _all_sources())

    def test_unknown_source_key_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            select_next("all", {"authored": [], "smuggled": [_brief("authored", "x")]})
        self.assertIn("unknown backlog source", str(ctx.exception))


class EmptinessTests(TestCase):
    def test_no_sources_at_all_is_none(self) -> None:
        self.assertIsNone(select_next("all", {}))

    def test_every_source_empty_is_none(self) -> None:
        self.assertIsNone(select_next("all", {"authored": [], "refined": [],
                                              "candidate": []}))

    def test_a_missing_key_is_an_empty_source_not_an_error(self) -> None:
        self.assertEqual(select_next("all", {"candidate": [_brief("candidate", "c1")]}
                                     ).source_ref, "c1")


class PurityTests(TestCase):
    def test_repeated_calls_return_the_same_choice(self) -> None:
        sources = _all_sources()
        first = select_next("all", sources)
        for _ in range(5):
            self.assertIs(select_next("all", sources), first)

    def test_the_input_mapping_is_not_mutated(self) -> None:
        sources = _all_sources()
        snapshot = {k: list(v) for k, v in sources.items()}
        select_next("all", sources)
        self.assertEqual({k: list(v) for k, v in sources.items()}, snapshot)

    def test_lower_priority_sources_are_not_consumed_when_a_higher_one_has_work(self) -> None:
        """Sources are lazy on purpose: an authored brief must short-circuit the
        scan so a full candidate ranking is never built when it cannot win."""
        touched: list[str] = []

        def spy(name, items):
            for item in items:
                touched.append(name)
                yield item

        sources = {"authored": spy("authored", [_brief("authored", "a1")]),
                   "refined": spy("refined", [_brief("refined", "r1")]),
                   "candidate": spy("candidate", [_brief("candidate", "c1")])}
        self.assertEqual(select_next("all", sources).source_ref, "a1")
        self.assertEqual(touched, ["authored"])
