"""Two processes appending to one ledger must not both claim the same sequence.

`append` derives the next sequence from `MAX(sequence)` in THIS process's own
projection.sqlite. Each process holds its own projection, so without a cross-process
lock two of them read the same maximum and both write N+1.

That is not hypothetical. On 2026-09-04 two gateway subprocesses started concurrently
and each appended sequence 8139 -- two different `invocation.failed` events for the
same invocation, because each booting gateway independently ran its recover-in-flight
path over the shared ledger. The result is a stream whose rebuild raises, and a corrupt
ledger makes the gateway UNSTARTABLE: `ExecutionLedger.__init__` rebuilds whenever the
projection is stale.

The `threading.RLock` that was there is real but insufficient -- it is per-process.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Appends N job.expired events for distinct jobs. Distinct jobs matter: the reducer
# rejects a second terminal transition on one job, which would mask a sequence
# collision behind a different error.
_CHILD = """
import sys
from hearth.execution import new_execution_event, new_job_id, new_request_id
from hearth.execution.ledger import ExecutionLedger

root, tag, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
ledger = ExecutionLedger(root)
for index in range(count):
    ledger.append(new_execution_event(
        "request.accepted", request_id=new_request_id(), job_id=new_job_id(),
        principal={"type": "irc_account", "id": "derek", "authenticated": True},
        source={"transport": "irc", "adapter": "BotHerder"},
        operation="llm.chat",
        desired={"idempotency_key": "probe:%s:%d" % (tag, index),
                 "arguments": {"prompt": "hello"}}))
"""


def _read_sequences(events_path: Path) -> list[int]:
    return [json.loads(line)["sequence"]
            for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]


class InterprocessAppendTest(unittest.TestCase):
    def test_two_processes_appending_produce_a_contiguous_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ledger"
            root.mkdir()
            per_child = 12
            children = [
                subprocess.Popen(
                    [sys.executable, "-c", _CHILD, str(root), tag, str(per_child)],
                    cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for tag in ("a", "b")
            ]
            outcomes = [(child.wait(timeout=180), child.stderr.read().decode("utf-8", "replace"))
                        for child in children]
            for code, err in outcomes:
                self.assertEqual(code, 0, f"child failed: {err[-800:]}")

            sequences = _read_sequences(root / "events.ndjson")
            self.assertEqual(len(sequences), per_child * 2)
            # The invariant the ledger's own rebuild() enforces: position == sequence.
            self.assertEqual(sequences, list(range(1, per_child * 2 + 1)),
                             "sequences collided or skipped -- the append lock did not hold")

    def test_the_repaired_stream_still_rebuilds(self) -> None:
        """A contiguous stream is necessary but not sufficient; the reducer must accept it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ledger"
            root.mkdir()
            children = [
                subprocess.Popen(
                    [sys.executable, "-c", _CHILD, str(root), tag, "8"],
                    cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for tag in ("c", "d")
            ]
            for child in children:
                self.assertEqual(child.wait(timeout=180), 0,
                                 child.stderr.read().decode("utf-8", "replace")[-800:])

            # Force the path that fails on a corrupt stream: no projection, so __init__
            # must rebuild from events.ndjson alone.
            (root / "projection.sqlite").unlink(missing_ok=True)
            from hearth.execution.ledger import ExecutionLedger
            ExecutionLedger(root)  # raises ExecutionLedgerError if the stream is bad


if __name__ == "__main__":
    unittest.main()
