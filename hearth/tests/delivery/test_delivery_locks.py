"""Locks for what the delivery slice observed (2026-10-03, first slice, laps 1-5; docs/rnd-log.md rows of that date).

Each test names the work id or row that earned it. Sources are small excerpts of the files those briefs pinned
(commit de666ef75), models' answers are the stored delivery-output.json of the same works; nothing reads runs/.
"""
from __future__ import annotations

import copy
import html
import io
import json
import unittest
from pathlib import Path

from hearth.delivery import contract, sourcemap
from hearth.delivery.render import render
from hearth.delivery.sourcemap import SourceMap, file_map, locate, render_for_model
from hearth.delivery.verify import verify_legacy

FX = Path(__file__).resolve().parent / "fixtures"
SKIPS = ("fleet/bankedfire_linux.py", "bankedfire_skips_excerpt.py")
PERCEPTION = ("perception/service.py", "perception_service_excerpt.py")
LABCFG = ("host/lab-configurations.toml", "lab_configurations_excerpt.toml")
FIXTURES = {"a59bad05": SKIPS, "d8f8c68f": PERCEPTION, "f6038274": PERCEPTION, "7a5acea4": LABCFG}


def source_map(spec) -> SourceMap:
    path, name = spec
    return SourceMap("fixture", "0" * 40, [file_map(path, (FX / name).read_bytes())])


def stored(work: str):
    return (json.loads((FX / f"{work}.output.json").read_text(encoding="utf-8")),
            (FX / f"{work}.brief.json").read_bytes())


class LocatorQuoteCharacterTests(unittest.TestCase):
    def test_html_entities_in_a_quote_resolve_as_normalized(self) -> None:
        """work_a59bad05 (27B): under the JSON schema it wrote &quot; &gt; &apos; for the characters themselves."""
        sm = source_map(SKIPS)
        for quote in ('add_skip(str(detail[&quot;source_ref&quot;]), f&quot;dispatch-failed: {detail.get(&apos;submit_error&apos;)}&quot;)',
                      'str(r.get(&quot;until&quot;, &quot;&quot;)) &gt; stamp'):
            with self.subTest(quote=quote):
                loc = locate(sm, quote)
                self.assertEqual((loc.path, loc.match), ("fleet/bankedfire_linux.py", "normalized"))
                self.assertIn(html.unescape(quote), sm.files[0].lines[loc.start - 1])
        # Three quotes were entity-written; its separate fuzzy quote remains unresolved.
        out, brief = stored("a59bad05")
        _, man = render(out, brief, sm)
        self.assertEqual(man["repairs"]["normalized_quote"], 3)
        self.assertEqual(man["unsupported"], ["s0.p0.q0"])
        self.assertEqual(man["claims"][0]["candidate"]["reason"], "fuzzy_quote")

    def test_swapped_or_dropped_double_quotes_resolve_as_normalized(self) -> None:
        """work_d8f8c68f (8B): it swaps the double quotes for ' or drops them (11 of 18 quotes were repaired)."""
        sm = source_map(PERCEPTION)
        swapped = locate(sm, "region = body.get('region')")                       # source: body.get("region")
        dropped = locate(sm, "- POST /ocr                -> {ok: true, text: ..., tokens: [...]}")
        for loc in (swapped, dropped):
            self.assertEqual((loc.path, loc.match), ("perception/service.py", "normalized"), loc)
        self.assertIn('body.get("region")', sm.files[0].lines[swapped.start - 1])
        self.assertIn("POST /ocr", sm.files[0].lines[dropped.start - 1])
        # a quote with a changed letter still does not pass for a normalized one
        self.assertNotEqual(locate(sm, "region = body.get('regions')").match, "normalized")


class ShortQuoteFloorTests(unittest.TestCase):
    def test_a_one_word_quote_that_repeats_is_missing_and_counted(self) -> None:
        """work_f6038274 (8B): quotes such as `image` (a parameter name) occur everywhere; read as exact support they
        proved nothing. 12 of its 21 claims."""
        sm = source_map(PERCEPTION)
        loc = locate(sm, "image")
        self.assertEqual((loc.match, loc.path), ("missing", ""))
        self.assertGreater(loc.occurrences, 1)
        out, brief = stored("f6038274")
        _, man = render(out, brief, sm)
        self.assertEqual(man["repairs"]["short_ambiguous_quote"], 12)
        short = [c for c in man["claims"] if c["match"] == "missing" and c.get("ambiguous")]
        self.assertEqual(len(short), 12)
        self.assertTrue(all(c["id"] in man["unsupported"] for c in short))

    def test_a_short_quote_that_occurs_once_still_resolves(self) -> None:
        """The floor is for ambiguity, not length: `SKIP_DAYS = 7` (work_a59bad05) is 13 characters and one line."""
        sm = source_map(SKIPS)
        loc = locate(sm, "SKIP_DAYS = 7")
        self.assertEqual((loc.match, loc.occurrences), ("exact", 1))
        self.assertEqual(sm.files[0].lines[loc.start - 1], "SKIP_DAYS = 7")
        self.assertEqual(locate(sm, "SKIP_DAYS = 14").match, "missing")   # one changed number is another claim


class FuzzyIdentifierTests(unittest.TestCase):
    QUOTE = "self._send_json(HTTPStatus.OK, {ok: True, text: reply_text, duration_ms: dt_ms})"

    def test_a_fuzzy_quote_with_other_identifiers_is_not_support(self) -> None:
        """work_8168fd9f (8B, am4-tool-4070ti): it wrote `{ok: True, text: reply_text, duration_ms: dt_ms}`, a line
        the source does not have; the locator matched it at fuzzy:0.91 to the line that says `"text": res` and the
        claim counted as supported. A quote whose identifiers differ from the line it lands on is unsupported."""
        sm = source_map(PERCEPTION)
        self.assertEqual(locate(sm, self.QUOTE).match, "missing")
        # an identifier added, none swapped: the swapped-word rule passes it, the identifier rule does not
        self.assertEqual(locate(sm, "self._send_json(HTTPStatus.OK, {ok: True, text: res, duration_ms: dt_ms, model_id: mid})").match,
                         "missing")
        out ={"summary": "The /ocr handler's reply.", "sections": [{"heading": "s1", "paragraphs": [
            {"text": "Without tsv the handler returns the text and the duration.", "quotes": [self.QUOTE]}]}]}
        _, man = render(out, stored("f6038274")[1], sm)
        self.assertEqual(man["unsupported"], [man["claims"][0]["id"]])

    def test_a_fuzzy_quote_whose_identifiers_are_all_in_the_line_is_only_a_candidate(self) -> None:
        """Even a plausible fuzzy location is a candidate for the judge, never resolved evidence."""
        sm = source_map(PERCEPTION)
        line = next(l for l in sm.files[0].lines if '"text": res' in l)
        loc = locate(sm, line.strip().replace(", ", ",  ", 1).replace("self._send_json", "self._send_jsn", 1))
        self.assertEqual(loc.match, "missing")                                   # a changed identifier: refused
        loc = locate(sm, line.strip().replace("HTTPStatus.OK, ", "", 1))
        self.assertEqual((loc.match, loc.path), ("missing", ""))
        self.assertIn(line, loc.candidate["source_text"])

    def test_a_swapped_plain_word_is_refused_and_fuzzy_quotes_keep_candidate_lines(self) -> None:
        """Review of W13: `res` is a plain word, so the identifier shape misses `result` for `res`; a swapped word
        (one the line lacks, in place of one the quote lacks) is refused. The faithful fuzzy quotes of the stored
        8B answers (work_d8f8c68f, work_8168fd9f: `Exposes:` joined to a docstring line, `true` for `True`, ' for ")
        retain candidate source lines without counting as resolved."""
        sm = source_map(PERCEPTION)
        self.assertEqual(locate(sm, "self._send_json(HTTPStatus.OK, {ok: true, text: result, duration_ms: dt_ms})").match,
                         "missing")
        for quote, want in (("self._send_json(HTTPStatus.OK, {ok: true, text: res, duration_ms: dt_ms})", '"text": res'),
                            ("Exposes: - GET  /v1/models -> list of available models", "/v1/models"),
                            ("psm = body.get('psm', 6)", 'body.get("psm", 6)')):
            with self.subTest(quote=quote):
                loc = locate(sm, quote)
                self.assertEqual((loc.match, loc.path), ("missing", ""))
                self.assertIn(want, loc.candidate["source_text"])
                self.assertEqual(loc.candidate["reason"], "fuzzy_quote")


class UnresolvedCandidateTests(unittest.TestCase):
    def test_elided_perfect_score_is_unresolved_but_literal_ellipsis_is_exact(self) -> None:
        sm = SourceMap("fixture", "0" * 40, [file_map("x.py", b'def write():\n    payload = {"answer": 42}\n    return payload\n# literal ... present\n')])
        loc = locate(sm, 'payload = { ... }')
        self.assertEqual((loc.match, loc.path, loc.start), ("missing", "", 0))
        self.assertEqual(loc.candidate["match"], "fuzzy:1.00")
        self.assertEqual(loc.candidate["reason"], "elided_quote")
        self.assertIn('"answer": 42', loc.candidate["source_text"])
        self.assertEqual(locate(sm, "literal ... present").match, "exact")
        output = {"summary": "Payload.", "sections": [{"heading": "Payload", "paragraphs": [
            {"text": "The writer creates a payload.", "quotes": ['payload = { ... }']}]}]}
        md, man = render(output, stored("a59bad05")[1], sm)
        self.assertIsNone(man["claims"][0]["resolved"])
        self.assertEqual(man["unsupported"], ["s0.p0.q0"])
        self.assertEqual(man["verification"]["deterministic"]["state"], "fail")
        self.assertIn("unresolved elided_quote", md)
        contract.check_manifest(man)
        for change in ({"match": "exact"}, {"resolved": {"path": "x.py", "start_line": 2, "end_line": 2}}):
            bad = copy.deepcopy(man)
            bad["claims"][0].update(change)
            self.assertTrue(contract.validate_manifest(bad))

    def test_generation_budget_is_optional_bounded_and_not_boolean(self) -> None:
        brief = json.loads(stored("a59bad05")[1])
        self.assertEqual(contract.validate_brief(brief), [])
        for budget in (1, 6000, 16384):
            brief["generation"] = {"max_tokens": budget}
            self.assertEqual(contract.validate_brief(brief), [])
        for generation in ({"max_tokens": 0}, {"max_tokens": 16385}, {"max_tokens": True},
                           {"max_tokens": "6000"}, {}, {"max_tokens": 6000, "other": 1}):
            brief["generation"] = generation
            self.assertTrue(contract.validate_brief(brief))


class TruncatedQuoteTests(unittest.TestCase):
    SQ = "am4-vllm = { status = "

    def test_a_quote_cut_at_the_first_double_quote_resolves_to_the_line_in_the_named_block(self) -> None:
        """Wave 2 sizing runs (27B): it stops a TOML quote at the first double quote, `omen-dense-27b = { status = `;
        short and repeated in every block, 22 of them were rejected though the paragraph names the block. (`am4-vllm = { status = `
        is 22 characters, under the floor; the 27-character omen-dense-27b form is already exact and picked by the hint.)"""
        sm = source_map(LABCFG)
        lines = sm.files[0].lines
        self.assertEqual(locate(sm, self.SQ).match, "missing")                    # no hint: no single block
        self.assertEqual(locate(sm, self.SQ, hint="The backends are listed.").match, "missing")
        night = locate(sm, self.SQ, hint="Under configuration.tool-night the 27B stays live.")
        day = locate(sm, self.SQ, hint="In configuration.day the 27B is declared live.")
        self.assertEqual((night.match, night.truncated, night.occurrences), ("normalized", True, 1))
        self.assertEqual((night.start, night.end, day.start, day.end), (33, 33, 18, 18))
        self.assertTrue(lines[night.start - 1].startswith(self.SQ))
        # a quote that begins no line is not a prefix quote: the short floor still applies
        self.assertEqual(locate(sm, "{ status = ", hint="configuration.day").match, "missing")

    def test_a_prefix_quote_is_counted_as_its_own_repair_and_validates(self) -> None:
        sm = source_map(LABCFG)
        out = {"summary": "The 27B seat in two configurations.", "sections": [{"heading": "Seats", "paragraphs": [
            {"text": "In configuration.day the AM4 27B is declared live.", "quotes": [self.SQ]},
            {"text": "In configuration.tool-night AM4 is declared absent, and the tool seat live.", "quotes": [self.SQ, "am4-tool-4070ti = { status = "]}]}]}
        md, man = render(out, stored("f6038274")[1], sm)
        self.assertEqual(man["repairs"], {"truncated_quote": 2, "ambiguous_quote": 1})
        self.assertEqual([c["resolved"]["start_line"] for c in man["claims"]], [18, 33, 34])
        self.assertEqual([c.get("truncated") for c in man["claims"]], [True, True, None])   # the 4070ti line is exact, in two blocks
        self.assertEqual(man["unsupported"], [])
        self.assertIn('> am4-vllm = { status = "live", context_tokens = 16384', md)   # the source line is printed
        contract.check_manifest(man)
        bad = copy.deepcopy(man)
        bad["repairs"] = {"normalized_quote": 2, "ambiguous_quote": 1}
        self.assertTrue(contract.validate_manifest(bad))
        contract.selfcheck(out=io.StringIO())   # the example's truncated claim is real (de666ef:437) and its controls reject

    def test_a_code_prefix_that_does_not_stop_at_a_string_is_not_evidence(self) -> None:
        """Review of W13: the observation is a cut at the first double quote. In code many lines begin alike, so a
        12-character beginning that stops anywhere else (`except (OSError,` in load_skips) proves nothing."""
        sm = source_map(SKIPS)
        for quote, hint in (("except (OSError,", "load_skips returns no rows when the file is unreadable."),
                            ("now = now or", "candidate_exclusions stamps the current time.")):
            with self.subTest(quote=quote):
                self.assertEqual(locate(sm, quote, hint=hint).match, "missing")

    SRC = (b'def save_slots(p):\n    p = slots_path(); tmp = p.with_suffix(".tmp")\n\n\n'
           b'def add_skip(p):\n    rows = []\n    rows.append(1)\n    tmp = p.with_suffix(".tmp"); tmp.write_text(rows)\n\n\n'
           b'def keep(p):\n    tmp = p.with_suffix(".keep")\n\n\n'
           b'def rotate(p):\n    tmp = p.with_suffix(".old")\n    tmp = p.with_suffix(".new")\n\n\n'
           b'def both(p):\n    q = p; tmp = p.with_suffix(".a")\n    tmp = p.with_suffix(".b")\n')

    def test_a_prefix_quote_needs_one_line_and_no_other_hit_in_the_one_block_the_paragraph_names(self) -> None:
        """Review of W13 (shape of fleet/bankedfire_linux.py@de666ef, where `tmp = p.with_suffix(` begins line 437 in
        add_skip and sits mid-line at 99): the paragraph's block picks the line; a second line beginning alike, a
        mid-line hit in the block, or two named blocks (of any size) leave it missing."""
        sm = SourceMap("fixture", "0" * 40, [file_map("fleet/x.py", self.SRC)])
        q = "tmp = p.with_suffix("
        hit = locate(sm, q, hint="add_skip writes the rows through a temporary file.")
        self.assertEqual((hit.match, hit.truncated, hit.start), ("normalized", True, 8))
        for hint in (None, "Each function writes a temporary file.",                      # no block named
                     "rotate keeps the old file and the new one.",                         # two lines begin alike
                     "both writes two temporary files.",                                   # a mid-line hit in the block
                     "keep and add_skip each write a temporary file."):                    # two blocks, sizes 2 and 4
            with self.subTest(hint=hint):
                self.assertEqual(locate(sm, q, hint=hint).match, "missing")


class PickTests(unittest.TestCase):
    QUOTE = 'omen-dense-27b = { status = "live", context_tokens = 65536, parallel_slots = 2, max_tokens = 16384 }'

    def test_a_repeated_quote_is_cited_in_the_block_the_paragraph_names(self) -> None:
        """work_7a5acea4 (27B): the same backend line sits in every [configuration.*.backends] block; the claim about
        tool-night was cited at the day block until the paragraph text steered it."""
        sm = source_map(LABCFG)
        lines = sm.files[0].lines

        def block(loc):
            return next(s["name"] for s in sm.files[0].symbols if s["start"] <= loc.start <= s["end"] and s["kind"] == "section")

        day = locate(sm, self.QUOTE, hint="In configuration.day the 27B is declared with 65536 context tokens.")
        night = locate(sm, self.QUOTE, hint="Under configuration.tool-night the 27B keeps 65536 context tokens.")
        self.assertEqual((day.occurrences, night.occurrences), (2, 2))
        self.assertEqual(block(day), "configuration.day.backends")
        self.assertEqual(block(night), "configuration.tool-night.backends")
        self.assertNotEqual(day.start, night.start)
        self.assertIn("omen-dense-27b", lines[night.start - 1])
        self.assertEqual(locate(sm, self.QUOTE).start, day.start)    # no hint: the first hit in map order


class PacketTests(unittest.TestCase):
    def test_the_quote_packet_has_no_symbol_index_and_no_line_numbers(self) -> None:
        """work_7a5acea4 (27B): shown an index it quoted index rows (`configuration.day [section] 9-14`) as source."""
        sm = source_map(LABCFG)
        plain = render_for_model(sm, numbered=False, symbols=False)
        self.assertNotIn("SYMBOLS", plain)
        self.assertNotIn("[section]", plain)
        self.assertNotIn("CODE:" + "\n001|", plain)
        self.assertFalse(any(line[:3].isdigit() and line[3:5] == "| " for line in plain.splitlines()), plain[:300])
        self.assertIn("[configuration.day.backends]", plain)          # the source itself is all there
        full = render_for_model(sm)
        self.assertIn("SYMBOLS", full)
        self.assertIn("configuration.day [section]", full)
        self.assertIn("001| ", full)
        self.assertEqual(render_for_model(sm, numbered=False, symbols=False), plain)   # byte-stable for the prefix cache


class DoctoredQuoteTests(unittest.TestCase):
    """Delivery task 11's negative control: an invented number must not reach a verdict."""

    def test_a_doctored_quote_is_unsupported_and_the_state_is_fail(self) -> None:
        sm = source_map(SKIPS)
        out, brief = stored("a59bad05")
        _, clean = render(out, brief, sm)
        self.assertEqual(clean["unsupported"], ["s0.p0.q0"])  # stored fuzzy quote is already unresolved
        for old, new in (("SKIP_DAYS = 7", "SKIP_DAYS = 14"),
                         ('indent=2)); os.replace', 'indent=4)); os.replace')):
            with self.subTest(change=new):
                doctored = copy.deepcopy(out)
                hit = [q for sec in doctored["sections"] for p in sec["paragraphs"] for q in p["quotes"] if old in q]
                self.assertEqual(len(hit), 1, "the fixture quote this control doctors")
                for sec in doctored["sections"]:
                    for p in sec["paragraphs"]:
                        p["quotes"] = [q.replace(old, new) for q in p["quotes"]]
                _, man = render(doctored, brief, sm)
                self.assertEqual(len(man["unsupported"]), len(clean["unsupported"]) + 1)
                bad = next(c for c in man["claims"] if new in c["quote"])
                self.assertEqual((bad["match"], bad["resolved"]), ("missing", None))
                self.assertIn(new, bad["quote"])
                self.assertEqual(man["verification"]["deterministic"]["state"], "fail")


class DoorValidatesRendererTests(unittest.TestCase):
    def test_every_stored_answer_renders_a_manifest_the_contract_accepts(self) -> None:
        """The door calls contract.check_manifest(render(...)[1]) and fails the work on ContractError; the answers of
        all four stored works, repaired or not, must pass it."""
        for work, spec in FIXTURES.items():
            with self.subTest(work=work):
                out, brief = stored(work)
                md, man = render(out, brief, source_map(spec))
                contract.check_manifest(man)
                self.assertEqual(contract.check_manifest_against_output(man, out), [])
                self.assertEqual(contract.check_manifest_against_brief(man, brief), [])
                self.assertTrue(md.strip())


class LineReferenceTests(unittest.TestCase):
    def setup_case(self):
        sm = SourceMap("fixture", "a" * 40, [file_map("src/x.py", b"same = 1\nsame = 1\n    " + b"x" * 500 + b"  \n\n")])
        brief = {"schema": "brief.v2", "substance": [{"id": "s1", "statement": "Describe the source."}],
                 "sources": [{"path": "src/x.py", "commit": "a" * 7}],
                 "form": {"quote_mode": "line_reference", "citations": "range"}}
        output = {"summary": "Source.", "sections": [{"heading": "Source", "paragraphs": [
            {"text": "The source repeats a line.", "quotes": ["src/x.py:2", "src/x.py:3", "src/x.py:4"]}]}]}
        return sm, brief, output

    def test_echo_uses_addressed_pinned_line_even_when_repeated_long_or_blank(self):
        sm, brief, output = self.setup_case()
        md, man = render(output, brief, sm)
        self.assertEqual([c["quote"] for c in man["claims"]], sm.files[0].lines[1:])
        self.assertEqual([c["resolved"]["start_line"] for c in man["claims"]], [2, 3, 4])
        self.assertEqual([c["quote_reference"] for c in man["claims"]], output["sections"][0]["paragraphs"][0]["quotes"])
        self.assertEqual(man["unsupported"], [])
        self.assertIn("line_reference", man["aids_used"])
        self.assertIn("> " + sm.files[0].lines[2] + " (src/x.py:3-3)", md)
        contract.check_manifest(man)
        self.assertEqual(contract.check_manifest_against_output(man, output), [])
        bad = copy.deepcopy(man)
        bad["claims"][0]["resolved"]["start_line"] = 1
        self.assertTrue(contract.validate_manifest(bad))

    def test_invalid_and_undeclared_references_never_search_or_fall_back(self):
        sm, brief, output = self.setup_case()
        for ref in ("src/x.py:0", "src/x.py:5", "src/x.py:2-3", "src/x.py:02", "src/x.py:+2",
                    "../src/x.py:2", "/src/x.py:2", "other.py:2", "same = 1", "src/x.py:2 "):
            with self.subTest(reference=ref):
                output["sections"][0]["paragraphs"][0]["quotes"] = [ref]
                _, man = render(output, brief, sm)
                self.assertEqual((man["claims"][0]["match"], man["claims"][0]["resolved"]), ("missing", None))
                self.assertNotIn("candidate", man["claims"][0])
                self.assertEqual(man["unsupported"], ["s0.p0.q0"])
                contract.check_manifest(man)
        output["sections"][0]["paragraphs"][0]["quotes"] = ["src/x.py:2"]
        for sources in ([{"path": "other.py", "commit": "a" * 7}], [{"path": "src/x.py", "commit": "b" * 7}]):
            brief["sources"] = sources
            _, man = render(output, brief, sm)
            self.assertEqual(man["unsupported"], ["s0.p0.q0"])

    def test_text_default_and_explicit_text_have_identical_report_and_claims(self):
        out, raw = stored("d8f8c68f")
        brief = json.loads(raw)
        first_md, first = render(out, brief, source_map(PERCEPTION))
        brief.setdefault("form", {})["quote_mode"] = "text"
        second_md, second = render(out, brief, source_map(PERCEPTION))
        self.assertEqual(first_md, second_md)
        first.pop("brief_sha256"); second.pop("brief_sha256")
        self.assertEqual(first, second)
        brief["form"]["quote_mode"] = "unknown"
        self.assertTrue(contract.validate_brief(brief))
        brief["form"]["quote_mode"] = "line_reference"
        brief.pop("sources")
        self.assertTrue(contract.validate_brief(brief))


class RungZeroArithmeticTests(unittest.TestCase):
    def test_the_sizing_slip_is_reported_with_the_recomputed_figure(self) -> None:
        """Sizing brief (delivery task 11): 2 x (0.30 x 65,536 + 0.5 x 16,384) was written as 51,968; it is 55,706."""
        said = "The reserve is 2 × (0.30 × 65,536 + 0.5 × 16,384) = 51,968 tokens."
        found = [f for f in verify_legacy(said)["rung0"]["findings"] if f["kind"] == "arithmetic_mismatch"]
        self.assertEqual(len(found), 1)
        self.assertIn("recomputed 55,706", found[0]["detail"])
        self.assertIn("51,968 stated", found[0]["detail"])
        self.assertEqual(verify_legacy(said)["rung0"]["state"], "fail")
        right = verify_legacy(said.replace("51,968", "55,706"))["rung0"]
        self.assertEqual(right["state"], "pass")

    def test_line_reference_blank_source_reaches_judge_without_hiding_it(self):
        from hearth.delivery.ladder import _paragraph_job, LadderError
        source = "before\n  \nafter\n"
        def read(path, start, end):
            return "\n".join(source.splitlines()[start - 1:end])
        claim = {"id": "s0.p0.q0", "text": "A blank line separates the text.",
                 "quote": "  ", "quote_reference": "a.py:2", "match": "exact",
                 "resolved": {"path": "a.py", "start_line": 2, "end_line": 2}}
        job = _paragraph_job("s0.p0", [claim], {"read": read})
        self.assertIn('Exact whitespace-only source line as JSON: "  "', job["quote"])
        self.assertIn("a.py:2-2", job["quote"])
        claim["resolved"].update(start_line=20, end_line=20)
        with self.assertRaisesRegex(LadderError, "inconsistent"):
            _paragraph_job("s0.p0", [claim], {"read": read})


if __name__ == "__main__":
    unittest.main()
