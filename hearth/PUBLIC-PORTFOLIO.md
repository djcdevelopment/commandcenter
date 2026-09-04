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
