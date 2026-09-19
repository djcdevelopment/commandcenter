# Public portfolio projection

`hearth.projection.public_portfolio` is the one-way privacy boundary between the
private HEARTH/MechNet ledgers and the public Steppe Integrations homepage.

It publishes fixed aggregate dimensions only. It never copies caller identity,
hostnames, addresses, ports, prompts, argument previews, task/job IDs, source paths,
errors, or exact timestamps. Weekly cells smaller than ten observations are
suppressed. The source-ledger prefix hashes make each candidate traceable to an exact
private input boundary without disclosing that input.

## Stage candidates

```powershell
powershell -ExecutionPolicy Bypass -File hearth\etc\stage-public-portfolio.ps1
```

Candidates land under `hearth/var/public-portfolio/`, which is gitignored. The script
also invokes the resume workspace's public-only claim exporter. It does not write to
the website checkout.

To install or refresh the nightly staging task for the current Windows user,
run the registration script once from PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File hearth\etc\register-public-portfolio-stage.ps1
```

Review and promotion happen from the `steppeintegrations-site` repository. A failed
stage or a machine that is offline leaves the last approved public snapshot intact.

## Research-seat receipts (ADR-0047)

Bench drivers talk to bespoke `llama-server` seats on ports the door never names
(127.0.0.1:8095 / :8096 today), so neither ledger above sees that traffic. The seat's
own `-lv 5 --log-file` under `hearth/var/swap-logs/` does, one `print_timing` block per
task. `python -m hearth.seats.harvest` turns those blocks into immutable
`seat.physical-attempt.v1` receipts in a third, append-only ledger,
`hearth/var/seats/receipts.ndjson`, with a cursor beside it. The `seat_harvest` kernel
timer runs it every 15 minutes and the stage script runs it once more before the
projection.

The fingerprint decides what is harvested, never the label: a log is a research seat
only when it has no `api_keys:` line and listens on a research-seat port. The
llama-swap-managed side seats carry the production key and ephemeral ports; the door
and the friend gate can reach them, so they are listed as skipped and never counted.
`--dry-run` prints that table and writes nothing.

Today the receipts are summarized on the private call-mix dashboard only
(`python -m hearth.projection.call_mix_dashboard --seats`). Publishing them as a
disjoint cohort on the public page is Phase B of ADR-0047 and has not been executed.
