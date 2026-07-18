# DECISIONS-PENDING sync recommendation — 2026-07-18

**Provenance:** produced by **am4-moe (gpt-oss-120b, resident)** via `tag:second-opinion`
(88s, first real triage workload through the brain), cross-referencing the packed
`DECISIONS-PENDING.md` against ground truth supplied from the execution log. Claude
review notes at the bottom. **This is a recommendation for Derek — the tracker is his
decision register and has not been edited.**

| # | Item (short) | Tracker status | Actual status now | Recommended edit |
|---|---|---|---|---|
| 1 | Two-ledgers bounded-contexts (ADR-0010) | DONE | DONE | no change |
| 2 | record_event double-write (ADR-0011) | DONE | DONE | no change |
| 3 | CQRS plan steps 2–4 | DONE | DONE | no change |
| 4 | known_good/known_bad models guard | DONE | DONE | no change |
| 5 | AM4 B70 bring-up (planner :8080 / critic :8081) | RESOLVED | **SUPERSEDED** — resident moe holds both cards; planner/critic demoted to pin-only | update note: superseded by resident-moe decision 2026-07-18 |
| 6 | Knowledge-guard bug fix | DONE | DONE | no change |
| 7 | Gateway reload (guard + commander tools) | DONE | DONE | no change |
| 8 | 24-pour idle campaign harvest/synthesis | PARTIAL | DONE (harvest + auto-sweep timer live; 64 runs / 145 branches drained) | close as DONE |
| 9 | Tailscale in machine loop (ADR-0014) | DONE | DONE | no change |
| 10 | Tailscale admin hygiene | DONE | DONE | no change |
| 11 | No human-facing services on conductor tailnet | DONE | DONE | no change |
| 12 | Fold ops loops into gateway (ADR-0015) | DONE | DONE | no change |
| 13 | Repo-aware local_generate (files=) | PINNED | **DONE** — shipped 2026-07-16, live, files_packed manifest | close (remove pinned) |
| 14 | Ollama interception proxy (:11434) | PINNED | PARTIAL — sentinel slice 0 live, proxy still pending data | keep PINNED, note sentinel collecting |
| 15 | Fleet builds targeting non-conductor repo | PINNED | unchanged | keep PINNED |

**Superseded by the resident-moe decision:** item 5 — the original planner/critic
bring-up as active lanes is obsolete; both are pin-only under the am4-moe residency
(stop `b70-moe` first for a deliberate planner run).

**Claude review notes:** the item numbering/statuses above reflect the moe's reading of
the packed tracker; the ADR references (0010/0011/0014/0015) and the pinned items ③/④
match the known register. Commit hashes the moe cited from the tracker body were not
independently re-verified — spot-check before applying edits. Items 5, 8, and 13 are the
three real state changes; the rest is confirmation.
