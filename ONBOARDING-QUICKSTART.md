# commandcenter — 5-minute onboarding

You are looking at a **local, self-hosted AI-agent fleet**: drop a task in, competing
build-outs run across VMs, get scored, and every hop is captured/traced. This is the
condensed quickstart. The full contract lives in `CLAUDE.md` on the canonical host —
read that next if you're going to build here.

**Canonical repo:** `claude@100.74.110.91:~/work/commandcenter` (host alias: cc-conductor).
Everything below assumes you `ssh claude@100.74.110.91` first. The copy at
`C:\work\commandcenter` on this Windows box is a **deprecated stub** — don't edit it,
don't run anything from it.

## 1. Start up / check the network is alive

The fleet is already-running systemd `--user` services on cc-conductor, not something
you boot per-session. Just verify it's up:

```bash
ssh claude@100.74.110.91
systemctl --user is-active commandcenter-conductor.service   # the brain — must be "active"
systemctl --user is-active commandcenter-api.service         # serves the dashboard on :8080
systemctl --user is-active idea-pipeline.service              # speak->board pipeline (may be inactive, that's OK)
```

If `commandcenter-conductor.service` isn't active:
```bash
systemctl --user start commandcenter-conductor.service
journalctl --user -u commandcenter-conductor.service -n 50   # if it won't stay up
```

Live visual state: **http://100.74.110.91:8080/fleet-dashboard.html**

## 2. Submit a request

Work items are plain markdown files dropped into `~/work/commandcenter/inbox/`.
The filename (minus `.md`) becomes the run id — must be unique, or the run is skipped.

For this local checkout, the executable workflow slice lives in:

```bash
python -m tools.workflow.engine --inbox inbox --runs runs --scenario happy
```

That path materializes:

- `runs/<id>/events.jsonl`
- `runs/<id>/state.json`
- `runs/<id>/board.json`
- `runs/<id>/otel-events.jsonl`

Fastest way to see it work — run a canned sample:
```bash
cd ~/work/commandcenter
cp inbox/samples/hello-build.md inbox/my-first-run.md
```

To write your own, the plan body is instructions for a coding agent, and **must end**
with the literal done-marker line (keep `{task_id}` verbatim — the worker substitutes it):
```
<your task, written as clear instructions for a coding agent>
When finished, append a line `## DONE: {task_id}` to ~/.comms/progress.md.
```
Without that line the build waits the full timeout (~450s) before committing whatever
exists.

The daemon polls `inbox/` every ~3s and runs **plan → build (parallel workers) → assay
(scored winner)** automatically. Note: the inbox is processed **serially** — one plan
at a time, so a slow build blocks the queue.

## 3. Monitor the outcome

```bash
tail -f ~/work/commandcenter/conductor.log        # live: "[<id>] start ... DONE winner=..."
cat ~/work/commandcenter/runs/<id>/result.json     # winner + full scoreboard (grade, risk_score, file_count, has_tests...)
```

Other views:
- **Dashboard:** http://100.74.110.91:8080/fleet-dashboard.html
- **Traces:** Jaeger at `am4:16686`, service name `cc-conductor`, correlate by `trace_id`
- **Durable event log (every hop, every run, ever):** `idea-pipeline/capture.ndjson`
- **Competing build branches:** in `farmer-repo`, named `ccfarm/<id>/<worker>/lap1`

## Know before you build

- A scored winner branch is **not auto-merged** to mainline — promotion is a manual/open
  step, not automatic. Don't assume "winner" means "shipped."
- Every run spends real fleet compute (a Claude agent per worker) — mind cost when batching.
- Failed runs (e.g. grade F) are treated as valid data, not errors — don't hide or
  soften a bad score, that's the one hard rule in this codebase (see `CLAUDE.md`).
- Deprecated, do not run: `scripts/conductor.py`, `scripts/conductor_workflow.py`.

## Where to go deeper

| Need | File (on cc-conductor) |
|---|---|
| Full operating contract / values | `CLAUDE.md` |
| Onboard/debug a fleet VM | `FLEET-ADDRESSING-RUNBOOK.md` |
| Architecture / design intent | `MAF.png`, `MASTER-RECONCILIATION.md` |
| Latest health state | `HEALTHCHECK-REMEDIATION-2026-07-01.md` |
| Proven wins/capabilities | `WINS.md` |
