# 0057 — The execution projection is a derived index: it runs in WAL with synchronous NORMAL; the canonical event stream stays fsynced

**Status:** Implemented under hearth-env dev 2026-10-03 (flash 47fce27, 356007c; approved by claude, CLAUDE-APPROVED row 98); proposed for Derek's acceptance.

**Companion to:** ADR-0030 (HEARTH is the system of record for AI execution), ADR-0053 (environment axis; the merge and restart were made under dev), ADR-0054 (delivery; the itemized runs that exposed the cost).

## Context

During the itemized-delivery lap (2026-10-03, `docs/rnd-log.md`, row 10:42Z) small calls through the door were slow while the cards were idle. A tiny `local_generate` cost 2.1 s through the door against 43 ms of engine time. All seats together got about 5 calls a second. The first capacity corpus ran at 95 calls a minute with every card near 0% utilization (`CLAUDE-APPROVED.md` row 88).

The cause, found by an Opus agent profiling a scratch door (row 89; commit message of `47fce27`): the execution projection, `projection.sqlite`, is a derived index of the execution event stream `events.ndjson`. It was committed with SQLite's rollback journal and full sync, about 4 fsyncs per commit. A door `local_generate` makes 9 ledger appends, each committing the projection, all under the ledger's process-wide lock: about 57 fsyncs per call, which capped the door at about 5 calls a second at any concurrency. The stream is canonical: `_projection_is_stale()` rebuilds the projection after a lost commit.

## Decision

1. The projection runs in WAL journal mode with `synchronous=NORMAL`. An idle keeper connection is held open so that SQLite does not checkpoint on every per-operation close. `rebuild()` removes the `-wal` and `-shm` sidecar files together with the database (`47fce27`).
2. The canonical event stream stays fsynced on every append. The kernel ledger event, the offload observation and the capacity leases are unchanged (`47fce27`).
3. The staleness check that runs when the ledger is opened now runs under the cross-process append lock, so a process that opens the ledger while another is appending does not read a half-finished append as stale and rebuild (`356007c`). The loop that switches the database to WAL checks its deadline whether SQLite raises an error or returns the old mode silently, and raises `ExecutionLedgerError` naming the mode or error, instead of spinning at gateway start (`356007c`).

## Consequences

Measured, scratch door on an ext4 copy of production state with a stub engine of 41 ms (`47fce27` message): p50 latency at 1, 8 and 16 concurrent calls 273, 1553 and 2679 ms before, 133, 430 and 458 ms after; throughput at 8 and 16 concurrent 4.5 and 4.5 calls/s before, 12.3 and 11.6 after.

Measured, production door after the merge and the gateway restart at 11:49:04Z (row 98 and `RESULT.md` item 9 in `~/work/delivery-plan/evidence/itemized/`): a tiny call 0.20 s (was 2.1 s); 12 to 14 calls a second on the 30B alone and 7 to 8 on the Ti; the three fast seats at once with one item a call, 6.2 calls a second against 1.6 before. Leases and the kernel index still cost about 45 ms a call (row 98).

Measured, the race fixed by `356007c` (commit message): opening the ledger while a scratch writer appended triggered a full rebuild in 38 of 40 opens (12 of 40 on the base code); under the lock, 0 of 40. On production that case is the 06:45 morning report opening the door's ledger; each needless rebuild deletes the projection under the door (2.8 s at production size).

Review: a second Opus agent reviewed the first cold and ran the execution, local-work, delivery and kernel tests, six kill -9 rounds and two lost-tail cases on ext4; Claude read both diffs (row 98).

Not verified (row 98): a real power cut; whether a saturated door starves a second process on the append lock (50 ms polling, same as before the change).

Revert: reverting is safe because the old code reads a WAL database (row 98). Revert `356007c` and `47fce27` and restart the gateway, as the merge did to go live.  Reverting was not tried.
