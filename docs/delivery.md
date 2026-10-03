# Delivery reports: budgets, evidence and review

A caller supplies a `brief.v2`; the model returns `summary` and `sections` with paragraph `text` and `quotes`. The renderer stores a `delivery.v1` manifest and a report. Local-work candidates stop at `awaiting_review`. Read the complete report against the pinned source before recording an acceptance or rejection: deterministic success establishes form and reference resolution, not substance.

## Brief options

This fragment selects numbered-source references and a 6,000-token output budget; retain the complete substance criteria and pinned sources in the surrounding brief:

```json
{
  "form": {"quote_mode": "line_reference", "citations": "quote"},
  "generation": {"max_tokens": 6000}
}
```

For local-work, output-budget precedence is explicit submission `max_tokens`, then `brief.generation.max_tokens`, then the existing word-derived default (1.75 tokens per capped word plus 1,536 overhead, floor 2,048; uncapped default 8,192; bounded by the operation ceiling). The explicit or brief budget must still fit the route context and execution policy. Impossible budgets are refused; raising the budget is not permission to truncate a response and call it complete. Quote-heavy inventories need room for JSON and evidence as well as prose.

`form.quote_mode` defaults to `text`. Text quotes resolve exactly or through supported normalization; a fuzzy or elided match is unsupported even when its similarity score is 1.00. A candidate location and source excerpt can assist review but do not populate a resolved location. Literal ellipses actually present in the source can still match exactly. Review the recorded match, candidate location, `unsupported`, repairs and deterministic state separately.

With `line_reference`, each `quotes[]` string must be one canonical repository-relative `path:N`, for example `perception/service.py:123`. No ranges, `/input/` aliases or invented source paths. The model sees numbered source; the renderer checks the declared pinned file and line bounds, echoes the exact original line into the report/manifest, and retains the input as `quote_reference`. Invalid references stay unsupported. To cite several lines, emit several entries. The verification/judge input contains the echoed source text.

An exact reference to a closing brace, unrelated function, or “Exposes:” heading can still accompany a false or incomplete paragraph. These are observed failures, not hypothetical ones. Judge each paragraph's attribution and the full brief's coverage; do not substitute quote counts for a source read.

## Revision adoption

A revised answer is mechanically eligible only when unsupported quotes strictly decrease and no new rung-0 failure kind or finding appears. An exact comparison proves quote-only repairs preserve summary, headings, paragraph text and order. Rewritten prose requires a separate non-author 27B coverage assessment to account for the original summary and claims, with evidence for retained/corrected claims or an explicit withdrawal reason. Both answers and the assessment remain recorded. Missing, invalid, refused or incomplete coverage keeps the original with a named reason; no weaker judge silently replaces the 27B.

The coverage assessment guards revision adoption, not final acceptance. A retained answer still requires a frontier or human substance verdict under the applicable environment policy. Local assessments, deterministic checks and recorded verdicts are distinct evidence.

## DeepAgents schema reports

Run from the DeepAgents checkout using its environment and a fresh destination outside immutable historical runs:

```bash
python run_linux_delivery.py --source /absolute/pinned-checkout/perception/service.py \
  --destination /absolute/new-experiment-directory --task-file /absolute/full-task.txt \
  --backend am4-tool-5070 --report --report-delivery schema \
  --brief /absolute/perception.brief.v2.json --quote-mode line_reference \
  --max-output-tokens 6000
```

The complete task remains in both model stages, even if an old brief's criterion was truncated. The supplied brief preserves all criteria, form and generation settings. It must declare exactly the canonical source path; its commit must resolve to bytes identical to the frozen input. A word-cap override conflicting with the supplied brief is refused. Explicit `--max-output-tokens` overrides the brief generation budget; otherwise existing runner defaults apply. Both the tool loop and schema completion request temperature 0, recorded in the run manifest.

In line mode, filesystem tools can read the same frozen bytes at `/input/<basename>` and `/<canonical/repo/path>`. The canonical alias is readonly, and citations still use repository-relative `path:N`. The run and delivery record the `source_path_alias` aid. This addresses the observed failure where a tool loop searched the citation path instead of its `/input/` copy.

For an intentionally smaller final schema source pack, add `--schema-source-ranges '35-49,55-72'`. This is valid only for line-reference schema reports. Ranges must be positive, ordered, nonoverlapping and within the pinned file. Numbers retain original positions. The tool loop still has the whole frozen source; only the final schema SOURCE block is excerpted. The prompt explicitly labels excerpts, retains the full task/findings, and cannot use absence from an excerpt as evidence of absence from the file. The manifest records scope, ranges, path, pin and source hash, and delivery records `source_excerpt`.

Excerpts are an explicit aid, not a fallback after refusal. The full task, loop findings, schema and output reserve still consume context; a reduced SOURCE block can still exceed the seat limit. Record that refusal. Recheck completeness against the full source, especially when omitted lines were unavailable to the final pass. Inspect both `final-message.txt` and the rendered report: schema conversion can discard correct loop findings. Missing support never becomes a successful run merely because all selected references exist.

## Capability records

Only the main lab-rnd checkout writes its hash-chain registry. A worktree may inspect or use a temporary registry, but must not import into shared state. From the main checkout, after reviewing the evidence:

```bash
python3 research/cli.py import-delivery --run-notes /home/derek/work/delivery-plan/evidence --dry-run
python3 research/cli.py import-delivery --run-notes /home/derek/work/delivery-plan/evidence
python3 research/cli.py show --delivery
```

DeepAgents history is opt-in: add `--deepagents-runs /absolute/exact-run-directory` to import that run, or a parent to select its immediate run directories. No DA history is selected by default. A frontier `substance-review.json` sidecar must bind the run ID, delivery brief hash, raw output hash, grade-file hash and every original criterion statement/status/evidence. Old runs without `brief.json` use the manifest's full task as their sole recoverable criterion.

The display keeps resolved/unsupported counts, deterministic state, per-criterion local assessments and frontier decisions distinct. Missing assessment means unjudged. Multiple judge results and disagreements remain visible. DA frontier assessments are imported as assessments; they do not invent an ADR-0048 local-work verdict. Preserve failed generations and unread reports in the surrounding experiment evidence even when no delivery manifest exists to import.
