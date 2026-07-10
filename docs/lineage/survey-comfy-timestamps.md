# Survey: `C:\work\comfy` — timestamp archaeology

*Sonnet agent survey, 2026-07-10, local read-only walk. Method: script-driven
timestamp analysis (creation + modification times), not doc reading — per
Derek's instruction "parse and categorize by file time created and last
updated for most effective pattern recognition." Report preserved verbatim,
lightly condensed. Scan CSV lived in session scratchpad; regenerate from the
walker script approach described below if needed.*

---

## Totals and the noise/signal split

- **242,867 files / 77.4 GB** on disk — but dominated by non-authored bulk: a vendored Flatpak/Proton Linux-container runtime and full Steam/Proton Valheim client+server installs under `fieldlab/autonomous/state/{client01,client02,server}`.
- **Strip the runtime-state noise and the authored/working-artifact footprint is 3,325 files / ~2.7 GB.** Anyone reading raw file counts without this split would badly misread the effort curve.

## Creation bursts by day — the campaign narrative

| Day | Authored files | What lit up |
|---|---|---|
| 2026-06-30 | 16 | `data/`, `docs/` — quiet staging **the evening before the repo existed** |
| 2026-07-01 | ~3,261 | Founding day: root README, control-surface handoff, quest-log mod, guild trackers. 17 commits |
| 2026-07-02 | 15 | `recipes/`, `quest_select_design/` (3 commits) |
| 2026-07-03 | 112 | `network/` fork born — "network research fork and telemetry scaffold" |
| 2026-07-04 | 833 + ~214k runtime | NetworkSense debug panel + MCP gateway built; the multi-client Valheim Docker/Steam-Headless/Proton lab provisioned in ~4 hours (one-time bulk install, not authorship) |
| 2026-07-05–07 | 71/95/49 | quiet trickle — `runs/` only |
| 2026-07-08 | 402 | Big session: Lumberjacks priority path, Discord Search Exporter, all three `comfy-*-analysis` dossiers born same day (16 commits) |
| 2026-07-09 | 41 + ~20.8k reprovision | Netcode-replacement pivot: feasibility doc, worklog, staged test program, I0 netcode map, I1 probe PASS, dashboard, P1/P2 rungs (14 commits); `fieldlab-session-audit` born |
| 2026-07-10 | 57 | P3 ownership-pin rung: built, armed, PASSED, disarmed, retro + 2 ADRs — 15 commits and climbing |

Git cross-check: root repo 69 commits, first 2026-07-01 02:49, matching the file-timestamp window exactly. `scratch/` contains vendored reference clones dated 2025-06-11 and 2026-05-06 (outside material, not campaign authorship). `erasave/` holds real Valheim world-saves dated back to 2024-10-26 — the project is grounded in a multi-year-old live community, not a synthetic scenario.

## Recency heat map

Everything outside `fieldlab/` and `network/` is dormant — created in its phase window and untouched since. `handoffs/` froze at **07-08 12:10, the exact minute the mission pivoted** from Lumberjacks priority-path to netcode replacement. Hot areas (modified minutes before the survey): `fieldlab/docs/adr/`, `retro/SESSION-RETRO-2026-07-10.md`, `status/` dashboard, `evidence/i2-pin/`, `network/mcp/var/ledger/events.jsonl`.

The 2026-07-10 tail sequence, read purely from mtimes: **code edits 03:04–03:08 → evidence capture (`ANALYSIS.md`, churn/pin `.jsonl`) 03:14–04:04 → dashboard/status refresh 04:09 → worklog 04:23 → ADRs 04:25 → session retro 04:25.** Build → capture evidence → document → retro, in one sitting, minutes apart.

## The pattern (working rhythm, from timestamps alone)

1. **Pre-stage before the repo exists.** Groundwork laid the evening before the first commit; even founding days aren't cold starts.
2. **Artifact-first, consistently.** Docs and evidence are written in the same sitting, immediately after the code that produced them — often within single-digit minutes. Not retrofitted.
3. **Two intensity classes of session.** Quiet days (a handful of files, 1 commit) alternate with intense days (dozens of artifacts, 14–17 commits), near-daily cadence.
4. **Analysis precedes and feeds builds, temporally.** The three Discord-dossier analysis dirs were built in one concentrated day, sandwiched between build campaigns — understanding-gathering as a distinct, dated phase.
5. **Handoff/waypoint discipline is a hard commit.** Each phase genuinely closes (clean stop timestamps), rather than being continuously appended to.
6. **Provisioning is separable from authorship in the data.** The two multi-ten-thousand-file spikes are infrastructure stand-ups, cleanly distinguishable from the low-volume, high-intent authored trickle.
7. **Old data pulled in as ground truth, not fabricated** (the 2024 world-saves).

**Significance for the lineage:** comfy (Jul 1–10, 69 commits) is the second 10-day campaign — Farmer (Apr 6–16, 81 commits) was the first — in an unrelated domain, with the same discipline legible in raw file times. The method transfers; it is not a property of one project.
