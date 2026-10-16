# 0071 — A rejection names its bucket

**Status:** Accepted (2026-10-16, Derek). History: Proposed (Track B of the "better labels" slice, 2026-10-16, filed by Claude Fable 5.1 under cc-c657d41c on
the plan `tender-gathering-fox` Lap 1 and `~/work/lab-rnd/research/labels/CONTRACT.md` section 2). The code is on the
flash branch `labels-door-20261016` and is not live until the door restarts after cap night 1, with preflight and
sole lease. Goal tag G-harness under G-lab. Extends ADR-0048 and enforces ADR-0069; nothing in ADR-0053 changes.

## Context

The lab grades a local model's work in three buckets (the measurement rule in `~/work/lab-rnd/AGENTS.md`, the HEARTH
method section 3): **form** (a sidecar or the reviewer repairs it; it is counted, not a verdict), **setup** (the brief,
the seat, thinking or the budget was wrong; one lever changes and the work reruns) and **substance** (a wrong answer
under a fair setup; the only bucket that counts against the model). The capability record O-1 asks for the substance
verdict apart from the form repairs, and H-1 asks whether the review minutes paid.

The census of the door's verdict records (Lap 0 of the plan, 2026-10-16) read 252 recorded verdicts and found 0
that carry a bucket. The field has been optional on `record_local_work_verdict` since harness-plan I1.6, the skill
never asked the reviewer for it, and a rejection whose bucket is not stated at review time cannot be recovered later:
a "rejected" without its bucket is the "failed, failed" the method forbids, and it makes every model look worse than
it measured. Two more labels the record needs were missing for the same reason: whether a person or an attended
frontier lane stood behind the verdict (ADR-0069 decided the rule for unattended Codex lanes and asked the door to
enforce it; the enforcement had not landed), and when a review began (the first read of the candidate was not an
event, so review elapsed time could only be guessed from the execution ledger's truncated argument previews). The
conditions a run was dispatched under (family, lane, serving profile, routes profile, lab configuration, procedure,
temperature) were only in the manifest, projected after the outcome, never on the dispatch event itself.

The precursor plan (`~/work/lab-rnd/research/precursor/CONTRACT.md`, ADR-0070) settled that labels, not models,
are the bottleneck of the capability record; this ADR fixes the four labels the door itself can record at the moment
they are true.

## Decision

Door-side, in `hearth/localwork/service.py` and the `record_local_work_verdict` / `get_local_work_artifact` tools:

a. **A rejection names its bucket.** `record_local_work_verdict(decision="rejected")` without `bucket` is refused
   with `a rejection names its bucket: form, setup or substance (ADR-0071)`; nothing is written. `accepted` and
   `superseded` keep the bucket optional (ADR-0069 item 5). The bucket stays the three-value enumeration.
b. **Every verdict records `attended`.** Absent on the call, the caller class sets it: `codex-cli` is unattended
   (`UNATTENDED_CALLERS`, ADR-0069 item 4); `claude-frontier`, `derek-human` and every other caller are attended. A
   caller may state `attended` itself and is recorded as it said; for an unattended class the amendment below applies.
c. **An unattended caller records a rejection only, and never on its own candidate.** `attended: false` with
   `accepted` or `superseded` is refused citing ADR-0069; so is any unattended verdict on a candidate whose
   `manifest.caller.submitted_by` equals the verdict's caller. Both refusals happen before anything is written.
d. **The labels travel on the events.** `bucket`, `attended`, `task_id` (the reviewing session's `cc-…` / `cx-…` id,
   optional) and `validator` are stored in `manifest.verdict` and copied onto both the `verification.recorded` and
   the `outcome.final` payloads, so a reader of history alone has them.

Two more events:

- **`artifact.fetched` `{by, status}`**, a new type in the operator history taxonomy (23 types; the contract enum
  `operator-history-event.v1` and `history.EVENT_TYPES` move together). `LocalWorkService.artifact(work_id,
  caller_id)` appends it once per (work, caller) when the run's history has no such event for that caller; the tool
  passes the authenticated caller id. A read never rewrites the manifest. The first one dates the start of that
  caller's review; the verdict event closes it.
- **`step.dispatched` carries the conditions**: `task_family, lane` (requested), `selected_lane,
  serving_profile_sha256, route_profile_sha256, procedure, lab_configuration, temperature`, read from the manifest's
  route and conditions through one helper (`_dispatch_conditions`) at every dispatch site (first attempt, structural
  repair, revision, revision coverage, the carried stages and the items stages). Identities, hashes and numbers only;
  a value the run does not have is null, never imputed. `temperature` is the one fractional value and travels as the
  operator history's fixed-precision decimal string (`"0.20"`, `canonical.decimal_str`): an event payload is an
  identity preimage and carries integers only; the manifest keeps the number.

## Consequences

- A post-restart rejection without a bucket cannot exist; the bucket coverage of new rejections is 100 % by
  construction, and the capability record can state "capable of X, not yet Y" from the door's own records.
- Unattended Codex night reviews close rejections themselves and are counted as unattended; the number of unattended
  acceptances on the ledger stays 0 by refusal, not by convention. The CLI policy (`cli-expectations.toml`) may now
  pre-approve `record_local_work_verdict` for Codex, as ADR-0069 foresaw; that is a separate change.
- Review elapsed time (`verdict_at − first_fetch_at`) and queue delay (`first_fetch_at − artifact.produced`) become
  derived from events for every post-restart run; the ledger-preview fallback remains for historic runs only.
- The `step.dispatched` event is larger by eight small fields; the materializer (Track C) reads the run's conditions
  before the outcome, and the precursor's query conditions may include `task_family` honestly.
- Callers that reject through other paths (`fleet/bankedfire_linux.py`, AMUX hand-off scripts, the local-work skills)
  must send a bucket; Track D updates the skill text. Until the door restarts nothing changes on the wire.
- Not decided here: which bucket a reviewer picks when two apply (the method says name the one that would change the
  next run). Whether `superseded` names a bucket is settled by the amendment below.

## References

- ADR-0048 — local-model repository work remains a candidate until a frontier verdict.
- ADR-0069 — an unattended Codex lane may record a rejection, never an acceptance, and never on its own candidate.
- ADR-0070 and `~/work/lab-rnd/research/precursor/CONTRACT.md` — the precursor plan that found labels, not models,
  to be the bottleneck.
- `~/.claude/plans/tender-gathering-fox.md` (Lap 1) and `~/work/lab-rnd/research/labels/CONTRACT.md` (section 2).

## Amendment 2026-10-16 (Derek, items 7a and 7b)

**7a. Codex CLI stays reject-only, enforced at the verdict handler.** For a caller whose class is unattended
(`UNATTENDED_CALLERS`, today `codex-cli`) `attended` is forced to false whatever the call says; decision b above no
longer lets that class record itself as attended. A call that passed `attended=True` has the attempt recorded as
`attended_declared: true` on `manifest.verdict` and on both the `verification.recorded` and `outcome.final` payloads,
so it is visible, not silent, and the ADR-0069 refusal of `accepted` and `superseded` then applies unchanged. The key
is absent when nothing was declared. Attended callers keep decision b: they may pass `attended=False` to record an
unattended verdict.

**7b. Superseded is a neutral lifecycle outcome, not a model miss.** It names no bucket (decision a stands). A model
success or failure rate computed over verdicts takes `accepted` and `rejected` only: `superseded` (and `failed`) stay
out of the denominator and are counted separately in operational reporting. In the door and its projections this was
already so (`hearth/delivery/procedures.py` shares `accepted / (accepted + rejected)`; the labels rollup counts each
decision by name; the precursor index puts `superseded` and `failed` in `nonbinary_excluded`); the test
`test_a_superseded_count_is_outside_the_accepted_share_denominator` pins it for the procedure table.
