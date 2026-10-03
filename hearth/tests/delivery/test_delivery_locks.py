"""Locks for what the delivery slice observed (2026-10-03, first slice, laps 1-5; docs/rnd-log.md rows of that date).

Each test names the work id or row that earned it. Sources are small excerpts of the files those briefs pinned
(commit de666ef75), models' answers are the stored delivery-output.json of the same works; nothing reads runs/.
"""
from __future__ import annotations

import copy
import html
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
        # the same stored answer, whole: three of its quotes were entity-written and none is missing
        out, brief = stored("a59bad05")
        _, man = render(out, brief, sm)
        self.assertEqual(man["repairs"]["normalized_quote"], 3)
        self.assertEqual(man["unsupported"], [])

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
        out = {"summary": "The /ocr handler's reply.", "sections": [{"heading": "s1", "paragraphs": [
            {"text": "Without tsv the handler returns the text and the duration.", "quotes": [self.QUOTE]}]}]}
        _, man = render(out, stored("f6038274")[1], sm)
        self.assertEqual(man["unsupported"], [man["claims"][0]["id"]])

    def test_a_fuzzy_quote_whose_identifiers_are_all_in_the_line_still_resolves(self) -> None:
        """The guard is for invented names: the same line with a dropped word and every identifier kept is fuzzy."""
        sm = source_map(PERCEPTION)
        line = next(l for l in sm.files[0].lines if '"text": res' in l)
        loc = locate(sm, line.strip().replace(", ", ",  ", 1).replace("self._send_json", "self._send_jsn", 1))
        self.assertEqual(loc.match, "missing")                                   # a changed identifier: refused
        loc = locate(sm, line.strip().replace("HTTPStatus.OK, ", "", 1))
        self.assertTrue(loc.match.startswith("fuzzy:") or loc.match == "normalized", loc)
        self.assertEqual(sm.files[0].lines[loc.start - 1], line)


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
        self.assertEqual((clean["unsupported"], clean["verification"]["deterministic"]["state"]), ([], "pass"))
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
                self.assertEqual(len(man["unsupported"]), 1)
                bad = next(c for c in man["claims"] if c["id"] == man["unsupported"][0])
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


if __name__ == "__main__":
    unittest.main()
