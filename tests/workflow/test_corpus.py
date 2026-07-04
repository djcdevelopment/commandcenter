from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.workflow.corpus import Corpus


def _write_events(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


def _event(event_id: str, timestamp: str | None = None) -> str:
    doc = {"event_id": event_id}
    if timestamp is not None:
        doc["timestamp"] = timestamp
    return json.dumps(doc)


class TestCorpusEnumerate:
    def test_deterministic_ordering_across_repeated_calls(self, tmp_path: Path) -> None:
        _write_events(tmp_path / "b" / "events.jsonl", [_event("b1")])
        _write_events(tmp_path / "a" / "events.jsonl", [_event("a1")])
        _write_events(tmp_path / "a" / "nested" / "events.jsonl", [_event("a2")])

        first = Corpus.enumerate(tmp_path)
        second = Corpus.enumerate(tmp_path)

        assert first.event_files == second.event_files
        # relative ordering must be lexical by relpath, not filesystem/OS dependent
        relpaths = [p.relative_to(tmp_path).as_posix() for p in first.event_files]
        assert relpaths == sorted(relpaths)

    def test_digest_stable_across_two_enumerations_of_identical_tree(self, tmp_path: Path) -> None:
        _write_events(tmp_path / "runs" / "run1" / "events.jsonl", [_event("e1"), _event("e2")])
        _write_events(tmp_path / "runs" / "run2" / "events.jsonl", [_event("e3")])

        first = Corpus.enumerate(tmp_path)
        second = Corpus.enumerate(tmp_path)

        assert first.corpus_digest == second.corpus_digest
        assert first.corpus_digest.startswith("sha256:")
        assert first.event_count == 3

    def test_digest_changes_when_a_file_gains_a_line(self, tmp_path: Path) -> None:
        events_path = tmp_path / "runs" / "run1" / "events.jsonl"
        _write_events(events_path, [_event("e1")])
        before = Corpus.enumerate(tmp_path)

        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(_event("e2") + "\n")
        after = Corpus.enumerate(tmp_path)

        assert before.corpus_digest != after.corpus_digest
        assert after.event_count == before.event_count + 1

    def test_digest_changes_when_a_file_disappears(self, tmp_path: Path) -> None:
        _write_events(tmp_path / "runs" / "run1" / "events.jsonl", [_event("e1")])
        keep_path = tmp_path / "runs" / "run2" / "events.jsonl"
        _write_events(keep_path, [_event("e2")])
        before = Corpus.enumerate(tmp_path)

        (tmp_path / "runs" / "run1" / "events.jsonl").unlink()
        after = Corpus.enumerate(tmp_path)

        assert before.corpus_digest != after.corpus_digest
        assert after.event_count == 1

    def test_blank_lines_dont_count_but_dont_break_parsing(self, tmp_path: Path) -> None:
        events_path = tmp_path / "runs" / "run1" / "events.jsonl"
        _write_events(events_path, [_event("e1"), "", "   ", _event("e2")])

        corpus = Corpus.enumerate(tmp_path)

        assert corpus.event_count == 2

    def test_watermark_is_max_timestamp_across_all_events(self, tmp_path: Path) -> None:
        _write_events(
            tmp_path / "runs" / "run1" / "events.jsonl",
            [_event("e1", "2026-01-01T00:00:00Z"), _event("e2", "2026-03-01T00:00:00Z")],
        )
        _write_events(
            tmp_path / "runs" / "run2" / "events.jsonl",
            [_event("e3", "2026-02-01T00:00:00Z")],
        )

        corpus = Corpus.enumerate(tmp_path)

        assert corpus.watermark == "2026-03-01T00:00:00Z"

    def test_watermark_none_when_no_event_has_a_timestamp(self, tmp_path: Path) -> None:
        _write_events(tmp_path / "runs" / "run1" / "events.jsonl", [_event("e1")])

        corpus = Corpus.enumerate(tmp_path)

        assert corpus.watermark is None

    def test_malformed_json_line_is_skipped_for_watermark_but_still_counted(self, tmp_path: Path) -> None:
        events_path = tmp_path / "runs" / "run1" / "events.jsonl"
        _write_events(events_path, ["not json at all", _event("e1", "2026-05-01T00:00:00Z")])

        corpus = Corpus.enumerate(tmp_path)

        assert corpus.event_count == 2
        assert corpus.watermark == "2026-05-01T00:00:00Z"

    def test_enumerate_single_file_root(self, tmp_path: Path) -> None:
        events_path = tmp_path / "events.jsonl"
        _write_events(events_path, [_event("e1")])

        corpus = Corpus.enumerate(events_path)

        assert corpus.event_files == (events_path,)
        assert corpus.event_count == 1

    def test_enumerate_missing_root_yields_empty_corpus(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"

        corpus = Corpus.enumerate(missing)

        assert corpus.event_files == ()
        assert corpus.event_count == 0
        assert corpus.watermark is None
