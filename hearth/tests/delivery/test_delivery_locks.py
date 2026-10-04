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

from hearth.delivery import carry, contract, sourcemap
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

    def test_ladder_reader_uses_source_map_line_numbers_and_preserves_bytes(self):
        from unittest import mock
        from hearth.delivery.ladder import _git_reader, _paragraph_job
        source = "a\fb\nc\n\n"
        with mock.patch("hearth.delivery.ladder.subprocess.run", return_value=mock.Mock(returncode=0, stdout=source.encode())):
            read = _git_reader("unused", {"sources": [{"path": "a.py", "commit": "0" * 40}]})
            self.assertEqual(read("a.py", 1, 100), source)
            self.assertEqual(read("a.py", 2, 2), "c\n")
            claim = {"id": "s0.p0.q0", "text": "Blank follows c.", "quote": "", "quote_reference": "a.py:3",
                     "match": "exact", "resolved": {"path": "a.py", "start_line": 3, "end_line": 3}}
            self.assertIn('Exact whitespace-only source line as JSON: ""', _paragraph_job("s0.p0", [claim], {"read": read})["quote"])

    def test_line_reference_blank_source_reaches_judge_without_hiding_it(self):
        from hearth.delivery.ladder import _paragraph_job, LadderError
        source = "before\n  \nafter\n"
        def read(path, start, end):
            return "".join(source.splitlines(keepends=True)[start - 1:end])
        claim = {"id": "s0.p0.q0", "text": "A blank line separates the text.",
                 "quote": "  ", "quote_reference": "a.py:2", "match": "exact",
                 "resolved": {"path": "a.py", "start_line": 2, "end_line": 2}}
        job = _paragraph_job("s0.p0", [claim], {"read": read})
        self.assertIn('Exact whitespace-only source line as JSON: "  "', job["quote"])
        self.assertIn("a.py:2-2", job["quote"])
        source = "before\n\n"
        job = _paragraph_job("s0.p0", [claim], {"read": read})
        self.assertIn('Exact whitespace-only source line as JSON: ""', job["quote"])
        claim["resolved"].update(start_line=20, end_line=20)
        with self.assertRaisesRegex(LadderError, "inconsistent"):
            _paragraph_job("s0.p0", [claim], {"read": read})


class CarryTests(unittest.TestCase):
    """Lap 17 B1: the carried-draft helpers, on made-up drafts and the proven reference forms."""

    def test_strip_removes_references_and_keeps_every_other_number(self):
        for before, after, refs in (
                ("x (17), y", "x, y", ["17"]),
                ("See lines 663-683 for it. Then at line 17 it ends.", "for it. Then it ends.", ["lines 663-683", "line 17"]),
                ("reads the file (160, 174-180) and L17 again", "reads the file and again", ["160, 174-180", "L17"]),
                ("see service.py:78 and host/x.toml:12-14", "see service.py and host/x.toml", ["78", "12-14"]),
                ("ctx 65536, 2 slots, k=1, v2.3 (65536) on 127.0.0.1:8710", "ctx 65536, 2 slots, k=1, v2.3 (65536) on 127.0.0.1:8710", []),
                ("`line 17` and \"lines 3-4\" stay", "`line 17` and \"lines 3-4\" stay", []),
                ("(1) first, (2) second", "(1) first, (2) second", []),
                ("flip (18\u219233) done", "flip done", ["18\u219233"]),
                # forms the 27B wrote in eight real drafts (2026-10-03); the seam closes without orphan marks
                ("ctx 65536 (day line 17; tool-night line 32).", "ctx 65536.", ["day line 17; tool-night line 32"]),
                ("max 16384 (TOML 17, 32); x (day 22; tool-night 37).", "max 16384; x.", ["TOML 17, 32", "day 22; tool-night 37"]),
                ("- L348: SKIP_DAYS=7.", "- SKIP_DAYS=7.", ["L348"]),
                ("- item (17)\n  continued (lines 3-4) here", "- item\n  continued here", ["17", "lines 3-4"]),
                ("writes .tmp then os.replace (429-437).", "writes .tmp then os.replace.", ["429-437"]),
                ("omen-dense-27b: 017/032 live; am4-vllm (018 -> 033).", "omen-dense-27b: live; am4-vllm.", ["017/032", "018 -> 033"]),
                ("collect_backends 191-210 reads it, defined at 469-510.", "collect_backends reads it, defined.", ["191-210", "469-510"]),
                ("lines 485 and 487 hold a (663\u2013761) b at S680-683 here", "hold a b here", ["lines 485 and 487", "663\u2013761", "S680-683"]),
                # slash-joined alternatives (the perception delivery of 2026-10-04 kept "/251", "/143" and "/206")
                ("default 8 L232-234/251. Success L141/143: ok", "default 8. Success: ok", ["L232-234/251", "L141/143"]),
                ("model=\"tesseract-ocr\" L186/206; ratio 3/4, 8/251 and an L2/3 cache stay", "model=\"tesseract-ocr\"; ratio 3/4, 8/251 and an L2/3 cache stay", ["L186/206"]),
                # values, enumerators, years, units and code stay
                ("slots (2), threads (8), the limit (8), in (2026), (live in both)", "slots (2), threads (8), the limit (8), in (2026), (live in both)", []),
                ("it does (1) read and (2) write; takes 2-3 days and 4-8 GB; L2 cache, an L4 GPU", "it does (1) read and (2) write; takes 2-3 days and 4-8 GB; L2 cache, an L4 GPU", []),
                ("dated 2026-10-03, qwen3.8-27b, x (default 6), y (day 17)", "dated 2026-10-03, qwen3.8-27b, x (default 6), y (day 17)", []),
                ("```\nx = f(17)  # line 3\n```", "```\nx = f(17)  # line 3\n```", [])):
            self.assertEqual(carry.strip_line_references(before), (after, refs), before)

    def test_split_joins_and_handles_edges(self):
        draft = "\nintro text.\n\nNotes\n" + "".join(f"- item {i} (L{i})\n" for i in range(30)) + "\nLong\n\n" + ("Sentence about it. " * 160)
        blocks = carry.split_draft(draft)
        self.assertEqual("".join(b["raw"] for b in blocks), draft)
        self.assertEqual([b["id"] for b in blocks], list(range(1, len(blocks) + 1)))
        self.assertEqual(blocks[0]["kind"], "text")                       # text before any heading
        self.assertEqual(sum(b["kind"] == "text" and b["text"].startswith("- item") for b in blocks), 30)
        self.assertTrue(all(len(b["text"]) <= 2400 for b in blocks))
        self.assertGreater(len([b for b in blocks if b["text"].startswith("Sentence")]), 1)
        out, rep = carry.assemble(blocks, {})
        self.assertEqual(contract.validate_output(out), [])
        self.assertEqual([s["heading"] for s in out["sections"]], ["Report", "Notes", "Notes (continued)", "Long"])
        self.assertEqual(rep["repairs"]["line_reference_stripped"], 30)
        self.assertNotIn("N:", out["summary"])

    def test_split_keeps_fences_items_and_rules_and_a_short_last_line(self):
        draft = "Notes\n\n```\n# not a heading\n- not an item\n\nx = 1\n```\n\n1. Run it\n\n---\n\nNo other endpoints\n"
        blocks = carry.split_draft(draft)
        self.assertEqual("".join(b["raw"] for b in blocks), draft)
        self.assertEqual([(b["kind"], b["text"][:12]) for b in blocks],
                         [("heading", "Notes"), ("text", "```\n# not a "), ("text", "1. Run it"), ("text", "---"), ("text", "No other end")])

    def test_report_part_carries_the_last_report_heading_on(self):
        blocks = carry.split_draft("# Verified notes\n\nA (12, 14).\n\n## Report:\n\nOne.\n\nTwo.\n")
        carried, notes, found = carry.report_part(blocks)
        self.assertEqual(([b["id"] for b in carried], [b["id"] for b in notes], found), ([3, 4, 5], [1, 2], True))
        self.assertEqual(carry.format_notes(notes), "# Verified notes\n\nA (12, 14).\n")
        out, rep = carry.assemble(carried, {}, notes_blocks=len(notes))
        self.assertEqual((rep["repairs"]["notes_blocks_not_carried"], "report_heading_missing" in rep["repairs"]), (2, False))
        carried, notes, found = carry.report_part(carry.split_draft("Head\n\nOne.\n"))
        self.assertEqual((len(carried), notes, found), (2, [], False))
        self.assertEqual(carry.report_part(carry.split_draft("# Report\n\nOne.\n"))[1:], ([], True))

    def test_report_part_names_only_a_report_heading_and_finds_a_report_line_inside_a_paragraph(self):
        notes = "Verified notes:\n\n- the report must show a (12).\n\n"
        for head in ("Report:", "REPORT", "**Report**", "## Final report", "Report (draft)", "Report: day vs tool-night"):
            carried, _, found = carry.report_part(carry.split_draft(notes + head + "\n\nThe body.\n"))
            self.assertEqual((found, carried[0]["id"], carried[-1]["text"]), (True, 3, "The body."), head)
            self.assertFalse(carry.assemble(carried, {})[0]["sections"][0]["heading"].endswith(":"), head)
        for draft in ("Report notes\n\n- a (12).\n\nThe body.\n", "Verified notes for the report:\n\n- a (12).\n\nThe body.\n",
                      "Reporting path\n\n- a (12).\n\nThe body.\n", notes + "The body.\n"):
            self.assertFalse(carry.report_part(carry.split_draft(draft))[2], draft)
        carried, notes_, _ = carry.report_part(carry.split_draft(notes + "Report\n\nA (12).\n\nReport\n\nB.\n\n## Limits of this report\n\nC.\n"))
        self.assertEqual(([b["text"] for b in carried], len(notes_)), (["Report", "B.", "Limits of this report", "C."], 4))
        for draft in ("Verified notes:\n\nNote one is line 12.\nReport:\nThe body.\n", "Verified notes:\n\n- a (12).\n**Report:**\nThe body.\n"):
            blocks = carry.split_draft(draft)
            carried, notes_, found = carry.report_part(blocks)
            self.assertEqual((found, [b["kind"] for b in carried], carried[-1]["text"], "".join(b["raw"] for b in blocks)),
                             (True, ["heading", "text"], "The body.", draft))
        carried, _, found = carry.report_part(carry.split_draft(notes + "Report:\n"))   # a heading with nothing after it
        self.assertTrue(found)
        with self.assertRaisesRegex(carry.CarryError, "no paragraph"):
            carry.assemble(carried, {})

    def test_assemble_caps_quotes_and_attach_answer_must_cover_every_block(self):
        blocks = carry.split_draft("Head\n\nA paragraph.\n\nAnother one.\n")
        out, rep = carry.assemble(blocks, {2: [f"q{i}" for i in range(11)], 3: []})
        self.assertEqual((len(out["sections"][0]["paragraphs"][0]["quotes"]), rep["repairs"]["quotes_beyond_cap_dropped"], rep["quotes"]), (8, 3, 8))
        self.assertEqual(carry.parse_attach("[block 2]\n> a = 1\n> say \"b\"\n[block 3]\n(none)\n", [2, 3]),
                         ({2: ["a = 1", 'say "b"'], 3: []}, {"json_unescaped_quote": 0, "quote_over_limit_dropped": 0}))
        self.assertEqual(carry.parse_attach('[block 2]\n> "a \\"x\\""\n', [2])[1], {"json_unescaped_quote": 1, "quote_over_limit_dropped": 0})
        self.assertEqual(carry.parse_attach(f"[block 2]\n> {'x' * 401}\n> ok\n", [2]), ({2: ["ok"]}, {"json_unescaped_quote": 0, "quote_over_limit_dropped": 1}))
        for bad, ids in (("[block 2]\n> a\n", [2, 3]), ("[block 2]\n(none)\n[block 9]\n(none)\n", [2]), ("[block 2]\nsome prose\n", [2]),
                         ("[block 2]\n(none)\n[block 3]\n", [2, 3])):           # the last: cut off after a block line
            with self.assertRaises(carry.CarryError):
                carry.parse_attach(bad, ids)

    def test_manifest_accepts_procedure_aids_and_carry_repair_keys(self):
        out, raw = stored("d8f8c68f")
        _, man = render(out, json.loads(raw), source_map(PERCEPTION))
        man.update(procedure="carry", aids_used=man["aids_used"] + ["thinking", "carried_draft"])
        man["repairs"].update(line_reference_stripped=4, quotes_beyond_cap_dropped=1, json_unescaped_quote=0)
        contract.check_manifest(man)
        man["procedure"] = "other"
        self.assertTrue(contract.validate_manifest(man))

    def test_assemble_strips_headings_drops_reference_only_blocks_and_agreement_reads_every_span(self):
        blocks = carry.split_draft("# " + "H" * 130 + " (lines 3-4)\n\nA statement (10, 20-22).\n\n(lines 7-8)\n")
        out, rep = carry.assemble(blocks, {2: ["q"]})
        self.assertEqual(contract.validate_output(out), [])
        self.assertLessEqual(len(out["sections"][0]["heading"]), 120)
        self.assertEqual(([p["text"] for p in out["sections"][0]["paragraphs"]], rep["blocks_empty_after_stripping_dropped"]), (["A statement."], [3]))
        man = {"claims": [{"id": "s0.p0.q0", "resolved": {"start_line": 10, "end_line": 10}},
                          {"id": "s0.p0.q1", "resolved": {"start_line": 21, "end_line": 25}}]}
        self.assertEqual([r["agrees"] for r in carry.line_reference_agreement(rep, man)], [None, True, None])
        man["claims"].pop()
        self.assertEqual(carry.line_reference_agreement(rep, man)[1]["agrees"], False)   # "20-22" has no quote


class SentenceUnitTests(unittest.TestCase):
    """Wave 8 (delivery-plan evidence/wave8/RESULT.md): sentence units in the attach pass lifted sizing from 9 quotes to 45."""

    DRAFT = ("Report\n\nFirst sentence here. Second one uses e.g. an abbreviation and (a note. Inside parens) too. "
             "Third has `a.b. C` code. Fourth says \"Quote. Next\" fine. It's done. Last one.\n")

    def test_sentence_units_rejoin_to_their_block_and_do_not_cut_inside_quotes_parentheses_or_abbreviations(self) -> None:
        """wave8: backoff 8 quotes -> 24 and perception 69 with units; a unit cut inside `a.b. C` or a quotation would carry
        half a claim. The block's raw text is the units' raw texts joined, and unit ids are '<block>.<n>'."""
        blocks = carry.split_draft(self.DRAFT)
        us = carry.units(blocks)
        self.assertEqual("".join(u["raw"] for u in us), blocks[1]["raw"])
        self.assertEqual([u["id"] for u in us], [f"2.{n}" for n in range(1, 7)])
        self.assertEqual([u["text"] for u in us][1:5], [
            "Second one uses e.g. an abbreviation and (a note. Inside parens) too.", "Third has `a.b. C` code.",
            'Fourth says "Quote. Next" fine.', "It's done."])
        self.assertTrue(all(u["block"] == 2 for u in us))
        self.assertEqual([len(b) for b in carry.batches(carry.split_draft("Report\n\n" + "Sentence here. " * 20 + "\n"))], [8, 8, 4])

    def test_a_dense_paragraph_splits_by_sentence_instead_of_dropping_quotes_past_the_cap(self) -> None:
        """wave8: sizing had 45 quotes against 9 (cap 8 per paragraph in wave 7). A block of 12 sentences with a quote each
        becomes 12 paragraphs, none dropped; the same quotes given to the block as a whole (a work stored before units) are
        cut at MAX_QUOTES and the drop is counted."""
        blocks = carry.split_draft("Report\n\n" + " ".join(f"Sentence number {n} states a fact." for n in range(12)) + "\n")
        out, rep = carry.assemble(blocks, {f"2.{n}": [f"q{n}"] for n in range(1, 13)})
        self.assertEqual(contract.validate_output(out), [])
        paras = out["sections"][0]["paragraphs"]
        self.assertEqual((len(paras), rep["quotes"], rep["repairs"]["quotes_beyond_cap_dropped"], rep["repairs"]["paragraphs_split_by_sentence"]), (12, 12, 0, 1))
        self.assertEqual([p["quotes"] for p in paras[:2]], [["q1"], ["q2"]])
        out, rep = carry.assemble(blocks, {2: [f"q{n}" for n in range(1, 13)]})
        self.assertEqual((len(out["sections"][0]["paragraphs"]), rep["quotes"], rep["repairs"]["quotes_beyond_cap_dropped"]), (1, 8, 4))
        self.assertNotIn("paragraphs_split_by_sentence", rep["repairs"])

    def test_a_sentence_without_a_quote_joins_the_one_before_it(self) -> None:
        """wave8: the attacher answers (none) for connective sentences; they stay in the paragraph of the claim they follow
        and a leading quote-less sentence joins the next."""
        blocks = carry.split_draft("Report\n\nLead in. Claim one holds. Then it is explained. Claim two holds. " + "Filler sentence. " * 9 + "\n")
        out, rep = carry.assemble(blocks, {"2.2": [f"a{n}" for n in range(5)], "2.4": [f"b{n}" for n in range(5)]})
        paras = out["sections"][0]["paragraphs"]
        self.assertEqual([p["text"][:12] for p in paras], ["Lead in. Cla", "Claim two ho"])
        self.assertEqual((rep["quotes"], rep["repairs"]["paragraphs_split_by_sentence"]), (10, 1))
        self.assertTrue(paras[0]["text"].endswith("Then it is explained."))


class StripWithSourcesTests(unittest.TestCase):
    """The line-reference rules that read the declared sources: wave 8 (sizing leaked "max_tokens (487)" and
    "collect_backends (192)") and the R5 and R6 reviews (a value in parentheses stays)."""

    @staticmethod
    def sizing_lines() -> dict:
        lines = ["x = 1"] * 500
        lines[191] = "def collect_backends(cfg):"
        lines[486] = "    cap = min(max_tokens, CAP)"
        return {"tools/ops/sizing_map.py": lines}

    def test_both_wave8_leaks_are_stripped_when_a_declared_line_holds_the_name(self) -> None:
        """wave8: `work_70cda00a` carried "max_tokens (487)" and "collect_backends (192)"; the model wrote 21 references and 19
        were stripped. Without sources the same text keeps them (a count noun before the number)."""
        text = "The cap comes from max_tokens (487) and the scan from collect_backends (192) as written."
        self.assertEqual(carry.strip_line_references(text, self.sizing_lines()),
                         ("The cap comes from max_tokens and the scan from collect_backends as written.", ["487", "192"]))
        self.assertEqual(carry.strip_line_references(text), (text, []))

    def test_a_number_with_no_source_evidence_is_left_as_written(self) -> None:
        """R5 review: a number that no declared line backs is a value until shown otherwise: another name on line 487, a
        value under 10, a prose word before the group, or the number sitting on a line with the name all leave it alone."""
        lines = self.sizing_lines()
        for text in ("It reads max_tokens (487) once.",):
            self.assertEqual(carry.strip_line_references(text, {"other.py": ["x = 1"] * 500}), (text, []))
        lines["tools/ops/sizing_map.py"][486] = "    max_tokens = 487"                # the number is the name's value
        self.assertEqual(carry.strip_line_references("It reads max_tokens (487) once.", lines), ("It reads max_tokens (487) once.", []))
        for text in ("It retries (3) times; parallel_slots (2) here.",):
            self.assertEqual(carry.strip_line_references(text, self.sizing_lines()), (text, []))

    def test_a_value_after_its_name_is_kept_and_the_same_number_after_a_prose_word_is_stripped(self) -> None:
        """R6 review: "psm (6)" kept against `.get("psm", 6)` (also `upscale (1)` against `upscale=1`); "status (200)" stripped
        when 200 stands only in a docstring or a comment, kept when the code gives `status` that value. Without sources the
        older rule removes the lone number, so it is the declared source that keeps the value."""
        code = {"perception/service.py": ['    psm = int(body.get("psm", 6))', "    upscale=1,", "    return status"]}
        text = "OCR runs with psm (6) and upscale (1) by default."
        self.assertEqual(carry.strip_line_references(text, code), (text, []))
        self.assertEqual(carry.strip_line_references(text), ("OCR runs with psm and upscale by default.", ["6", "1"]))
        doc = {"perception/service.py": ['def health():', '    """GET /health -> status: 200 OK."""', "    # status = 200", "    return {}"]}
        self.assertEqual(carry.strip_line_references("The endpoint answers with status (200) when well.", doc),
                         ("The endpoint answers with status when well.", ["200"]))
        live = {"perception/service.py": ["    status = 200"]}
        self.assertEqual(carry.strip_line_references("The endpoint answers with status (200) when well.", live),
                         ("The endpoint answers with status (200) when well.", []))


class NearAnchorTests(unittest.TestCase):
    """`work_aa6b13cd` (perception, wave 8): an ambiguous quote resolved to line 117, the /score branch's, where the paragraph
    spoke of /ocr (line 150). The nearest hit to the paragraph's uniquely placed quotes wins when no symbol is named."""

    SRC = ("def score(body):\n"
           "    if not body:\n"
           "        return {\"ok\": False, \"error\": \"bad image\"}\n"       # line 3: the first hit
           "    return {\"aesthetic\": 1}\n"
           "\n"
           "def ocr(body):\n"
           "    text = run_tesseract(body)\n"                                     # line 7: unique, the anchor
           "    if not body:\n"
           "        return {\"ok\": False, \"error\": \"bad image\"}\n"       # line 9: the one the paragraph means
           "    return {\"text\": text}\n")
    QUOTE = 'return {"ok": False, "error": "bad image"}'

    def sm(self) -> SourceMap:
        return SourceMap("fixture", "0" * 40, [file_map("perception/service.py", self.SRC.encode())])

    def test_locate_takes_the_hit_nearest_the_anchors_and_the_first_hit_without_them(self) -> None:
        sm = self.sm()
        hint = "An empty upload is refused with a bad-image error."
        plain = locate(sm, self.QUOTE, hint=hint)
        near = locate(sm, self.QUOTE, hint=hint, near=[("perception/service.py", 7)])
        self.assertEqual((plain.occurrences, plain.start, near.start), (2, 3, 9))
        self.assertEqual(locate(sm, self.QUOTE, hint=hint, near=[("other.py", 7)]).start, 3)       # no anchor in the hit's file
        named = locate(sm, self.QUOTE, hint="In score an empty upload is refused.", near=[("perception/service.py", 7)])
        self.assertEqual(named.start, 3)                                                           # a named symbol outranks the anchors

    def test_the_renderers_second_pass_places_the_ambiguous_quote_by_its_paragraphs_unique_quotes(self) -> None:
        out, raw = stored("d8f8c68f")
        brief = json.loads(raw)
        text = "The OCR route runs the engine and an empty upload is refused with a bad-image error."
        doc = {"summary": "s", "sections": [{"heading": "h", "paragraphs": [{"text": text, "quotes": ["text = run_tesseract(body)", self.QUOTE]}]}]}
        _, man = render(doc, brief, self.sm())
        lines = [(c["resolved"]["start_line"], c["match"]) for c in man["claims"]]
        self.assertEqual(lines, [(7, "exact"), (9, "exact")])
        alone = {"summary": "s", "sections": [{"heading": "h", "paragraphs": [{"text": text, "quotes": [self.QUOTE]}]}]}
        _, man = render(alone, brief, self.sm())
        self.assertEqual(man["claims"][0]["resolved"]["start_line"], 3)                             # no anchor: today's first hit


class SplitCheckTests(unittest.TestCase):
    """The check stage (check-probe/RESULT.md): the review turn answers with a `Changes` part and a `Report` part, 8 of 8 in the
    asked order; the report ends at a following `Changes` line and an answer with no report is refused (K1 review)."""

    def test_the_report_follows_the_last_report_line_and_the_changes_part_precedes_it(self) -> None:
        for head in ("Report", "## Report", "**Report**", "Report:", "Final report"):
            with self.subTest(head=head):
                self.assertEqual(carry.split_check(f"\nChanges\n- fixed s2\n- reworded s4\n\n{head}\n\nBody one.\n\nBody two.\n"),
                                 ("- fixed s2\n- reworded s4", "Body one.\n\nBody two."))
        self.assertEqual(carry.split_check("Report\n\nold\n\nReport\n\nnew\n")[1], "new")

    def test_the_report_ends_at_a_following_changes_line_and_that_part_is_never_the_report(self) -> None:
        """check-probe: a Changes part written after the report must not be delivered as report text."""
        self.assertEqual(carry.split_check("Report\n\nBody.\n\nChanges\n- x\n- y\n"), ("- x\n- y", "Body."))
        self.assertEqual(carry.split_check("Changes\n- first\nReport\n\nBody.\n\n**Changes**\n- later\n"), ("- first", "Body."))

    def test_an_answer_with_no_report_or_an_empty_one_is_refused(self) -> None:
        for answer in ("Changes\n- x\n\nThe corrected text without a heading.\n", "Report notes\n\nBody.\n", "Reporting path\nBody.\n",
                       "Report: day vs night\n\nBody.\n", ""):
            with self.subTest(answer=answer), self.assertRaisesRegex(carry.CarryError, "no line that says only `Report`"):
                carry.split_check(answer)
        for answer in ("Changes\n- x\nReport\n\n", "Report\n\nChanges\n- x\n"):
            with self.subTest(answer=answer), self.assertRaisesRegex(carry.CarryError, "Report part is empty"):
                carry.split_check(answer)

    def test_checked_draft_keeps_the_notes_and_reads_back_as_a_draft_and_report_diff_counts_by_exact_unit(self) -> None:
        """check-probe: the corrected report replaces the report part and the notes stay untouched; 58 sentences kept, 37
        changed, 1 added across the eight works (units compared by exact text)."""
        draft = "Notes\n\n- a (12).\n\nReport\n\nOld one.\n"
        checked = carry.checked_draft(draft, "New one.\nMore.\n")
        self.assertEqual(checked, "Notes\n\n- a (12).\n\nReport\n\nNew one.\nMore.\n")
        carried, notes, found = carry.report_part(carry.split_draft(checked))
        self.assertEqual((found, carry.format_notes(notes), carried[-1]["text"]), (True, "Notes\n\n- a (12).\n", "New one.\nMore."))
        with self.assertRaisesRegex(carry.CarryError, "no report heading"):
            carry.checked_draft("Just text.\n", "x")
        self.assertEqual(carry.report_diff(["A.", "B.", "B."], [{"text": "A."}, "C."]), {"kept": 1, "changed_or_removed": 2, "added": 1})


if __name__ == "__main__":
    unittest.main()
