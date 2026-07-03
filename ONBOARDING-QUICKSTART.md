# commandcenter — 5-minute onboarding

You are looking at a **local, self-hosted AI-agent fleet**: drop a task in, competing
build-outs run across VMs, get scored, and every hop is captured/traced. This is the
condensed quickstart. The full contract lives in `CLAUDE.md` on the canonical host —
read that next if you're going to build here.

## Two trees, don't confuse them

- **`C:\work\commandcenter` on OMEN (this repo)** — the workflow-ontology / belief-layer
  codebase you're reading right now. It is **not** a deprecated stub (an earlier version
  of this doc said so, before the 2026-07 pour made this the active target repo — that
  line was stale and has been removed). Its own origin is the private GitHub repo
  `djcdevelopment/commandcenter`. This is where you edit, run `python -m unittest
  discover -s tests/workflow`, and commit/push from.
- **`cc-conductor` (`claude@100.74.110.91:~/work/commandcenter`)** — a *different*
  codebase: the fleet orchestration engine that dispatches builder VMs. Everything in
  section 1–3 below runs there, over SSH, not on OMEN.

If you're here to **pour work through the fleet against this repo**, read
`docs/conductor-pour-howto.md` first — it covers filing work, restricting builders
(including excluding frontier/Claude builders in favor of local models), and pointing
the conductor at a non-default target repo. If you need conductor-side history visible
on GitHub, see `docs/conductor-github-sync.md` and `tools/ops/sync_conductor_to_github.py`
— the conductor never holds a GitHub credential; OMEN mirrors it there instead.

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

| Need | File | Lives on |
|---|---|---|
| Full operating contract / values | `CLAUDE.md` | cc-conductor |
| Onboard/debug a fleet VM | `FLEET-ADDRESSING-RUNBOOK.md` | cc-conductor |
| Architecture / design intent | `MAF.png`, `MASTER-RECONCILIATION.md` | cc-conductor |
| Latest health state | `HEALTHCHECK-REMEDIATION-2026-07-01.md` | cc-conductor |
| Proven wins/capabilities | `WINS.md` | cc-conductor |
| Pour work through the fleet at this repo | `docs/conductor-pour-howto.md` | OMEN (here) |
| Sync the conductor's own repo to GitHub | `docs/conductor-github-sync.md` | OMEN (here) |
| This repo's own pour history | `POUR-PLAN.md`, `POUR-STATUS.md`, `CODEX-POUR-ORCHESTRATOR.md` | OMEN (here) |
