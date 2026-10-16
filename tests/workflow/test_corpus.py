from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.workflow.corpus import Corpus


def _write_events(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


def _event(event_id: str, timestamp: str | None = None) -> str:
    doc = {"event_id": event_id}
    if timestamp is not None:
        doc["timestamp"] = timestamp
    return json.dumps(doc)


class TestCorpusEnumerate(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_deterministic_ordering_across_repeated_calls(self) -> None:
        _write_events(self.tmp_path / "b" / "events.jsonl", [_event("b1")])
        _write_events(self.tmp_path / "a" / "events.jsonl", [_event("a1")])
        _write_events(self.tmp_path / "a" / "nested" / "events.jsonl", [_event("a2")])

        first = Corpus.enumerate(self.tmp_path)
        second = Corpus.enumerate(self.tmp_path)

        self.assertEqual(first.event_files, second.event_files)
        # relative ordering must be lexical by relpath, not filesystem/OS dependent
        relpaths = [p.relative_to(self.tmp_path).as_posix() for p in first.event_files]
        self.assertEqual(relpaths, sorted(relpaths))

    def test_digest_stable_across_two_enumerations_of_identical_tree(self) -> None:
        _write_events(self.tmp_path / "runs" / "run1" / "events.jsonl", [_event("e1"), _event("e2")])
        _write_events(self.tmp_path / "runs" / "run2" / "events.jsonl", [_event("e3")])

        first = Corpus.enumerate(self.tmp_path)
        second = Corpus.enumerate(self.tmp_path)

        self.assertEqual(first.corpus_digest, second.corpus_digest)
        self.assertTrue(first.corpus_digest.startswith("sha256:"))
        self.assertEqual(first.event_count, 3)

    def test_digest_changes_when_a_file_gains_a_line(self) -> None:
        events_path = self.tmp_path / "runs" / "run1" / "events.jsonl"
        _write_events(events_path, [_event("e1")])
        before = Corpus.enumerate(self.tmp_path)

        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(_event("e2") + "\n")
        after = Corpus.enumerate(self.tmp_path)

        self.assertNotEqual(before.corpus_digest, after.corpus_digest)
        self.assertEqual(after.event_count, before.event_count + 1)

    def test_digest_changes_when_a_file_disappears(self) -> None:
        _write_events(self.tmp_path / "runs" / "run1" / "events.jsonl", [_event("e1")])
        keep_path = self.tmp_path / "runs" / "run2" / "events.jsonl"
        _write_events(keep_path, [_event("e2")])
        before = Corpus.enumerate(self.tmp_path)

        (self.tmp_path / "runs" / "run1" / "events.jsonl").unlink()
        after = Corpus.enumerate(self.tmp_path)

        self.assertNotEqual(before.corpus_digest, after.corpus_digest)
        self.assertEqual(after.event_count, 1)

    def test_blank_lines_dont_count_but_dont_break_parsing(self) -> None:
        events_path = self.tmp_path / "runs" / "run1" / "events.jsonl"
        _write_events(events_path, [_event("e1"), "", "   ", _event("e2")])

        corpus = Corpus.enumerate(self.tmp_path)

        self.assertEqual(corpus.event_count, 2)

    def test_watermark_is_max_timestamp_across_all_events(self) -> None:
        _write_events(
            self.tmp_path / "runs" / "run1" / "events.jsonl",
            [_event("e1", "2026-01-01T00:00:00Z"), _event("e2", "2026-03-01T00:00:00Z")],
        )
        _write_events(
            self.tmp_path / "runs" / "run2" / "events.jsonl",
            [_event("e3", "2026-02-01T00:00:00Z")],
        )

        corpus = Corpus.enumerate(self.tmp_path)

        self.assertEqual(corpus.watermark, "2026-03-01T00:00:00Z")

    def test_watermark_none_when_no_event_has_a_timestamp(self) -> None:
        _write_events(self.tmp_path / "runs" / "run1" / "events.jsonl", [_event("e1")])

        corpus = Corpus.enumerate(self.tmp_path)

        self.assertIsNone(corpus.watermark)

    def test_malformed_json_line_is_skipped_for_watermark_but_still_counted(self) -> None:
        events_path = self.tmp_path / "runs" / "run1" / "events.jsonl"
        _write_events(events_path, ["not json at all", _event("e1", "2026-05-01T00:00:00Z")])

        corpus = Corpus.enumerate(self.tmp_path)

        self.assertEqual(corpus.event_count, 2)
        self.assertEqual(corpus.watermark, "2026-05-01T00:00:00Z")

    def test_enumerate_single_file_root(self) -> None:
        events_path = self.tmp_path / "events.jsonl"
        _write_events(events_path, [_event("e1")])

        corpus = Corpus.enumerate(events_path)

        self.assertEqual(corpus.event_files, (events_path,))
        self.assertEqual(corpus.event_count, 1)

    def test_enumerate_missing_root_yields_empty_corpus(self) -> None:
        missing = self.tmp_path / "does-not-exist"

        corpus = Corpus.enumerate(missing)

        self.assertEqual(corpus.event_files, ())
        self.assertEqual(corpus.event_count, 0)
        self.assertIsNone(corpus.watermark)


if __name__ == "__main__":
    unittest.main()
