"""Research-seat receipts (ADR-0047).

A research seat is a bespoke ``llama-server`` that a benchmark driver talks to
directly on a port the HEARTH door never names (127.0.0.1:8095 / :8096 today).
The door ledgers never see that traffic. The seat's own ``--log-file`` does:
one ``print_timing`` block per task with the prompt and generated token counts
the server actually processed. This package turns those lines into immutable
``seat.physical-attempt.v1`` receipts in a third, append-only ledger
(``hearth/var/seats/receipts.ndjson``) that is never merged into the gateway or
execution ledgers.

- :mod:`hearth.seats.serverlog` parses a llama-server log (regexes and the
  elapsed-to-wall-clock derivation lifted from ``campaign/ff-probes``).
- :mod:`hearth.seats.receipts` defines and validates the receipt row and owns
  the locked append.
- :mod:`hearth.seats.harvest` is the idempotent harvester with its cursor and
  the research-seat eligibility fingerprint.
"""
