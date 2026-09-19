"""Parse a llama-server ``--log-file`` into per-task timing records.

The line shapes and the wall-clock derivation come from the campaign probes and
are restated here because ``hearth/`` must not import from ``campaign/``:

- ``ELAPSED_RE`` is ``campaign/ff-probes/ff_cell.py`` (llama-server stamps every
  line with elapsed ``MMMM.SS.mmm.uuu`` since process start; minutes are
  unbounded, ``3731.26.818.590`` has been observed).
- ``PROMPT_RE`` / ``EVAL_RE`` are ``campaign/ff-probes/c2_door_attribution.py``
  ``parse_server_log``.
- The epoch derivation is ``ff_cell.incumbent_epoch``: the log is truncated at
  every launch, so it describes exactly one process epoch, and
  ``mtime(log) - elapsed(last stamped line)`` pins the process start. The
  server runs under an S4U task whose StartTime is inaccessible, so this is
  the only anchor there is. ADR-0044 trusts it to about a second.

Nothing here reads a model path into a record: the load report's
``general.name``, the ``build N (hash)`` line and ``n_ctx_seq`` identify the
provider, and a drive-letter path never reaches a receipt.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_STAMP = r"^(\d+)\.(\d+)\.(\d+)\.(\d+)\s"
ELAPSED_RE = re.compile(_STAMP)
LAUNCH_RE = re.compile(
    _STAMP + r".*slot launch_slot_: id\s+(\d+) \| task (\d+) \| processing task")
PROMPT_RE = re.compile(
    _STAMP + r".*print_timing: id\s+(\d+) \| task (\d+) \| prompt eval time =\s+([\d.]+) ms /\s+(\d+) tokens")
EVAL_RE = re.compile(
    _STAMP + r".*print_timing: id\s+(\d+) \| task (\d+) \|\s+eval time =\s+([\d.]+) ms /\s+(\d+) tokens")
RELEASE_RE = re.compile(
    _STAMP + r".*slot\s+release: id\s+(\d+) \| task (\d+) \|")
BUILD_RE = re.compile(r"common_params_print_info: build (\d+) \(([0-9a-fA-F]+)\)")
NAME_RE = re.compile(r"general\.name\s+(?:str\s+)?=\s+(.+?)\s*$")
N_CTX_SEQ_RE = re.compile(r"\bn_ctx_seq\s+=\s+(\d+)")
API_KEYS_RE = re.compile(r"\bapi_keys:")
LISTEN_RE = re.compile(r"listening on http://127\.0\.0\.1:(\d+)")

#: How many stamped lines the epoch fingerprint covers. Microsecond stamps make
#: the prefix unique per launch, and 64 lines are written before the model has
#: finished loading, so the fingerprint is stable while the file grows.
PREFIX_LINES = 64


def elapsed_seconds(match: re.Match) -> float:
    """Seconds since process start from a stamp match (``MMMM.SS.mmm.uuu``)."""
    minutes, seconds, millis, micros = (int(match.group(i)) for i in range(1, 5))
    return minutes * 60 + seconds + millis / 1000.0 + micros / 1_000_000.0


@dataclass
class TaskRecord:
    slot: int
    task: int
    launched_s: float
    prompt_ms: Optional[float] = None
    prompt_n: Optional[int] = None
    eval_ms: Optional[float] = None
    predicted_n: Optional[int] = None
    timed_s: Optional[float] = None
    released_s: Optional[float] = None

    @property
    def timed(self) -> bool:
        return self.prompt_n is not None and self.predicted_n is not None


@dataclass
class ScanResult:
    path: Path
    build: Optional[str] = None
    model_name: Optional[str] = None
    n_ctx_seq: Optional[int] = None
    api_keyed: bool = False
    port: Optional[int] = None
    stamped_lines: int = 0
    last_elapsed_s: float = 0.0
    prefix_sha256: Optional[str] = None
    tasks: dict[tuple[int, int], TaskRecord] = field(default_factory=dict)
    size: int = 0
    mtime: float = 0.0

    @property
    def basename(self) -> str:
        return self.path.stem

    def later_launch_on_slot(self, record: TaskRecord) -> bool:
        return any(other.slot == record.slot and other.launched_s > record.launched_s
                   for other in self.tasks.values())


def _record(tasks: dict[tuple[int, int], TaskRecord], slot: int, task: int, at: float) -> TaskRecord:
    key = (slot, task)
    if key not in tasks:
        tasks[key] = TaskRecord(slot=slot, task=task, launched_s=at)
    return tasks[key]


def scan(path: Path) -> ScanResult:
    """Read one log in full and return its header, fingerprint and tasks.

    A full read every time is deliberate: bespoke seat logs are a few megabytes,
    and pairing a launch line with a timing block that may straddle any byte
    offset is simpler than resuming. Idempotency lives in the receipt identity,
    not in the read.
    """
    result = ScanResult(path=path)
    stat = os.stat(path)
    result.size = stat.st_size
    result.mtime = stat.st_mtime
    prefix = hashlib.sha256()
    prefix_count = 0
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            stamp = ELAPSED_RE.match(line)
            if stamp:
                result.stamped_lines += 1
                result.last_elapsed_s = elapsed_seconds(stamp)
                if prefix_count < PREFIX_LINES:
                    prefix.update(line.rstrip("\r\n").encode("utf-8", "replace"))
                    prefix.update(b"\n")
                    prefix_count += 1
                    if prefix_count == PREFIX_LINES:
                        result.prefix_sha256 = prefix.hexdigest()
            if result.build is None:
                m = BUILD_RE.search(line)
                if m:
                    result.build = f"build {m.group(1)} ({m.group(2)})"
                    continue
            if result.model_name is None:
                m = NAME_RE.search(line)
                if m:
                    result.model_name = m.group(1)
                    continue
            if result.n_ctx_seq is None:
                m = N_CTX_SEQ_RE.search(line)
                if m:
                    result.n_ctx_seq = int(m.group(1))
                    continue
            if not result.api_keyed and API_KEYS_RE.search(line):
                result.api_keyed = True
                continue
            if result.port is None:
                m = LISTEN_RE.search(line)
                if m:
                    result.port = int(m.group(1))
                    continue
            if not stamp:
                continue
            m = LAUNCH_RE.match(line)
            if m:
                slot, task = int(m.group(5)), int(m.group(6))
                if task >= 0:
                    _record(result.tasks, slot, task, elapsed_seconds(m))
                continue
            m = PROMPT_RE.match(line)
            if m:
                rec = _record(result.tasks, int(m.group(5)), int(m.group(6)), elapsed_seconds(m))
                rec.prompt_ms, rec.prompt_n = float(m.group(7)), int(m.group(8))
                rec.timed_s = elapsed_seconds(m)
                continue
            m = EVAL_RE.match(line)
            if m:
                rec = _record(result.tasks, int(m.group(5)), int(m.group(6)), elapsed_seconds(m))
                rec.eval_ms, rec.predicted_n = float(m.group(7)), int(m.group(8))
                rec.timed_s = elapsed_seconds(m)
                continue
            m = RELEASE_RE.match(line)
            if m:
                rec = _record(result.tasks, int(m.group(5)), int(m.group(6)), elapsed_seconds(m))
                rec.released_s = elapsed_seconds(m)
    return result


def epoch_start(result: ScanResult) -> datetime:
    """The process start, UTC: file mtime minus the last elapsed stamp."""
    mtime = datetime.fromtimestamp(result.mtime, tz=timezone.utc)
    return mtime - timedelta(seconds=result.last_elapsed_s)


def wall_clock(epoch: datetime, elapsed_s: float) -> datetime:
    return epoch + timedelta(seconds=elapsed_s)


def rfc3339(moment: datetime) -> str:
    """RFC3339 with a ``Z`` suffix and microseconds kept."""
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
