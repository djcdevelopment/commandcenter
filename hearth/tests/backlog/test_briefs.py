"""The Brief contract: round-trip, absent optionals, loud failure, submit surface.

Nothing here touches the filesystem — a Brief is data. The one test that reaches
outside the module introspects the REAL ``task_lane.submit_task`` signature, so
``submit_kwargs()`` cannot drift away from what it is supposed to splat into.
"""
from __future__ import annotations

import inspect
import json
from unittest import TestCase

from hearth.backlog import briefs
from hearth.backlog.briefs import SOURCES, Brief, parse, safe_slug
from hearth.toolsurface import task_lane


def _brief(**overrides) -> Brief:
    base = dict(
        slug="demo-brief",
        title="Demo brief",
        body="Do the thing.\n\nThen report.",
        builders=None,
        task_class=None,
        est_tokens=None,
        requires=(),
        max_age_s=None,
        source="authored",
        source_ref="0001-demo.md",
    )
    base.update(overrides)
    return Brief(**base)


class RoundTripTests(TestCase):
    def test_render_parse_round_trip_is_byte_identical_for_every_source(self) -> None:
        # One brief per source, each exercising a different optional-field mix,
        # so the round-trip property is proved across the whole rendered surface
        # rather than on one lucky shape.
        cases = {
            "authored": _brief(source="authored", source_ref="0001-demo.md"),
            "refined": _brief(source="refined", source_ref="refine-idea-abcd1234",
                              task_class="build", requires=("docs/plan.md",),
                              max_age_s=3600, builders=("cc-builder-2", "cc-builder-3")),
            "candidate": _brief(source="candidate", source_ref="cand:one",
                                task_class="proofing", requires=("proposals/x.md",),
                                est_tokens=1234),
        }
        for name, brief in cases.items():
            with self.subTest(source=name):
                rendered = brief.render()
                reparsed = parse(rendered)
                self.assertEqual(reparsed.render(), rendered)

    def test_parse_recovers_every_header_field(self) -> None:
        brief = _brief(task_class="build", est_tokens=99, requires=("a/b.md", "c/d.md"),
                       max_age_s=7200, builders=("cc-builder-2",))
        got = parse(brief.render())
        self.assertEqual(got.task_class, "build")
        self.assertEqual(got.est_tokens, 99)
        self.assertEqual(got.requires, ("a/b.md", "c/d.md"))
        self.assertEqual(got.max_age_s, 7200)
        self.assertEqual(got.builders, ("cc-builder-2",))
        self.assertEqual(got.body, brief.body)

    def test_parse_keeps_a_body_that_starts_with_a_blank_line(self) -> None:
        # render() ends the header with exactly one newline; parse() must eat
        # exactly that one, or a body whose first character is a newline comes
        # back short and the round trip silently loses a byte.
        brief = _brief(body="\nleading blank line matters\n")
        self.assertEqual(parse(brief.render()).body, brief.body)
        self.assertEqual(parse(brief.render()).render(), brief.render())


class HeaderShapeTests(TestCase):
    def test_absent_optionals_are_omitted_from_the_header(self) -> None:
        brief = _brief()
        header = brief.render().split("-->")[0]
        meta = json.loads(header.split("CCMETA", 1)[1].strip())
        self.assertEqual(list(meta), ["builders"],
                         "a brief with no optionals must render the BARE header")

    def test_bare_header_is_byte_identical_to_task_lanes_own(self) -> None:
        brief = _brief()
        expected = task_lane._ccmeta_header(list(task_lane.DEFAULT_BUILDERS))
        self.assertEqual(brief.render(), expected + brief.body)

    def test_declared_optionals_all_ride_the_header(self) -> None:
        brief = _brief(task_class="build", est_tokens=42, requires=("x/y.md",),
                       max_age_s=60)
        meta = json.loads(brief.render().split("CCMETA", 1)[1].split("-->")[0].strip())
        self.assertEqual(meta["task_class"], "build")
        self.assertEqual(meta["est_tokens"], 42)
        self.assertEqual(meta["requires"], ["x/y.md"])
        self.assertEqual(meta["max_age_s"], 60)

    def test_empty_requires_renders_as_an_absent_key_not_an_empty_list(self) -> None:
        meta = json.loads(_brief(requires=()).render()
                          .split("CCMETA", 1)[1].split("-->")[0].strip())
        self.assertNotIn("requires", meta)


class LoudFailureTests(TestCase):
    def test_missing_header_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse("just a body, no header")
        self.assertIn("no CCMETA header", str(ctx.exception))

    def test_non_json_header_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse("<!-- CCMETA\n{not json\n-->\nbody")
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_header_that_is_not_an_object_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse('<!-- CCMETA\n["a"]\n-->\nbody')

    def test_header_without_builders_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse('<!-- CCMETA\n{"task_class": "build"}\n-->\nbody')
        self.assertIn("builders", str(ctx.exception))

    def test_header_with_empty_builders_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse('<!-- CCMETA\n{"builders": []}\n-->\nbody')

    def test_empty_body_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _brief(body="   \n\t  ")
        self.assertIn("body", str(ctx.exception))
        with self.assertRaises(ValueError):
            parse('<!-- CCMETA\n{"builders": ["cc-builder-2"]}\n-->\n   \n')

    def test_source_outside_the_closed_enum_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _brief(source="whatever")
        self.assertIn("source must be one of", str(ctx.exception))
        self.assertEqual(SOURCES, ("authored", "refined", "candidate"))

    def test_unsafe_slug_raises(self) -> None:
        for bad in ("has space", "has/slash", "has\\back", "..", ""):
            with self.subTest(slug=bad), self.assertRaises(ValueError):
                _brief(slug=bad)

    def test_traversal_in_requires_raises(self) -> None:
        for bad in ("../secrets.md", "/etc/passwd", "C:/win.md"):
            with self.subTest(glob=bad), self.assertRaises(ValueError):
                _brief(requires=(bad,))

    def test_out_of_range_max_age_raises(self) -> None:
        with self.assertRaises(ValueError):
            _brief(max_age_s=0)
        with self.assertRaises(ValueError):
            _brief(max_age_s=10 ** 9)

    def test_empty_source_ref_raises(self) -> None:
        with self.assertRaises(ValueError):
            _brief(source_ref="  ")


class SubmitKwargsTests(TestCase):
    def test_keys_are_a_subset_of_the_real_submit_task_signature(self) -> None:
        allowed = set(inspect.signature(task_lane.submit_task).parameters)
        self.assertTrue(set(_brief().submit_kwargs()) <= allowed,
                        f"submit_kwargs emits keys submit_task does not accept: "
                        f"{set(_brief().submit_kwargs()) - allowed}")

    def test_prompt_is_the_rendered_brief(self) -> None:
        brief = _brief(task_class="build", requires=("a.md",))
        self.assertEqual(brief.submit_kwargs()["prompt"], brief.render())

    def test_requires_and_max_age_ride_the_kwargs(self) -> None:
        kwargs = _brief(requires=("a.md", "b.md"), max_age_s=120).submit_kwargs()
        self.assertEqual(kwargs["requires"], ["a.md", "b.md"])
        self.assertEqual(kwargs["max_age_s"], 120)

    def test_absent_requires_becomes_none_not_an_empty_list(self) -> None:
        # submit_task's validate_requires REJECTS an empty list; only None means
        # "no deliverables declared".
        self.assertIsNone(_brief(requires=()).submit_kwargs()["requires"])

    def test_est_tokens_is_derived_from_the_rendered_prompt_when_absent(self) -> None:
        brief = _brief(task_class="proofing")
        kwargs = brief.submit_kwargs()
        self.assertEqual(kwargs["est_tokens"],
                         task_lane.estimate_tokens(brief.render(), "proofing"))

    def test_declared_est_tokens_is_kept_verbatim(self) -> None:
        self.assertEqual(_brief(est_tokens=7).submit_kwargs()["est_tokens"], 7)

    def test_builders_none_lets_submit_task_choose(self) -> None:
        self.assertIsNone(_brief(builders=None).submit_kwargs()["builders"])
        self.assertEqual(_brief(builders=("cc-builder-9",)).submit_kwargs()["builders"],
                         ["cc-builder-9"])

    def test_plan_id_hint_names_the_source_and_stays_in_the_safe_charset(self) -> None:
        hint = _brief(source="candidate", slug="bbb_high",
                      source_ref="bbb_high").plan_id_hint()
        self.assertEqual(hint, "candidate-bbb_high")
        self.assertRegex(hint, r"^[A-Za-z0-9._-]+$")

    def test_plan_id_hint_is_bounded(self) -> None:
        hint = _brief(slug="a" * briefs.MAX_SLUG_CHARS).plan_id_hint()
        self.assertLessEqual(len(hint), briefs.MAX_PLAN_ID_HINT_CHARS)


class SafeSlugTests(TestCase):
    def test_preserves_underscores_and_case(self) -> None:
        # task_lane._slugify would give "bbb-high"; a candidate_id that loses its
        # underscore is a different id, and the plan_id would name the wrong thing.
        self.assertEqual(safe_slug("bbb_high"), "bbb_high")
        self.assertEqual(safe_slug("Mixed_Case.v1"), "Mixed_Case.v1")

    def test_collapses_unsafe_runs_and_trims_punctuation(self) -> None:
        self.assertEqual(safe_slug("backend_comparison:qwen:8x22b+vulkan"),
                         "backend_comparison-qwen-8x22b-vulkan")
        self.assertEqual(safe_slug("...leading and trailing..."),
                         "leading-and-trailing")

    def test_never_returns_empty_or_a_dotfile(self) -> None:
        self.assertEqual(safe_slug(":::"), "brief")
        self.assertFalse(safe_slug(".hidden").startswith("."))
