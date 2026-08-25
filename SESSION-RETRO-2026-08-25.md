# Session retro — 2026-08-25

**We built a render lane on the premise of a fast link, and then the link was needed
elsewhere — so the session's real work was discovering that the premise had been carrying
the design all along, and moving the reading to where the bytes are.**

## What this session was

A build that turned into a recovery, twice. It started as a straight implementation —
move BF6 rendering off AM4's RTX 5070 onto the two Arc Pro B70s already in OMEN, through
HEARTH's Operations/Jobs plane. It became a closure exercise (three named gaps, then
stop). Then the AM4↔OMEN copper cable was needed elsewhere, the SMB premise underneath the
whole design evaporated, and the last third of the session was re-deciding where the
reading happens.

Two of the session's most valuable findings were silent corruptions that every cheap check
passed. Neither was found by reasoning. Both were found by comparing against a known-good
output.

## What shipped

**commandcenter** — `dac3138..58181ad`, 17 commits, 49 files, +9681/−80, pushed.

| Commit | What |
|---|---|
| `ec49c14` | `media.render` operation on calibrated dual-B70 QSV lanes |
| `37a404a` | Mount the render lane on the door; refuse it in session 0 |
| `9f50685` | Interactive render agent; gateway stays the sole ledger writer |
| `487007f` | All-or-none variant promotion, rollback, and the BF6/OBS gate |
| `adad77e` | Bounded VBR rate control for horizontal, chosen on distribution |
| `0374a05` | BF6 sidecar bridge, review thumbnails, `bf6-dispatcher` caller |
| `cae299a` | Bounded VBR for vertical; complete draft-set parity |
| `b7d76c0` | Agent renders concurrently; Phase 6 coexistence accepted at 2 lanes |
| `f53ec54` | Prefer bus9 for a lone render; record accepted capacity |
| `1d73c0d` | Exact revision matching and scoped render cancellation |
| `58181ad` | Logon launchers for the render agent and BF6 bridge |

**bf6-highlights** (local-only repo) — `ec4766a..4a12a12`, 8 commits, 23 files, +2437/−85.

| Commit | What |
|---|---|
| `4e662bd` | Async render state machine, reconciler, recoverable claims |
| `fdb1c72` | Require exact revision matching on claims and results |
| `0ab1451` | Render state, lane, retry and reclaim in the review table |
| `6fe60fd` | Park non-blocking findings in a backlog |
| `50a7bab` | Producer-local extraction — OMEN reads raw video, AM4 no longer does |
| `d24eb72` | Use the QSV device form the render lane proved |
| `74b4a65` | Encode the proxy on CPU, and validate it before publishing |
| `4a12a12` | Register the OMEN side as interactive-logon tasks |

**Durable artifacts:** `docs/adr#0035`, `docs/adr#0036`, `docs/adr#0037`;
`hearth/media/` (11 modules); `omen-extractor/extract.py`;
`docs/RENDER-LANE-BACKLOG.md`; three interactive-logon scheduled tasks.
**Tests:** 1078 HEARTH · 41 worker · 12 extractor.

## The team retro — our collaboration across the seats

### Architect — Derek set the constraints, I did the load-bearing math

The design calls that held up were the ones grounded in a measurement rather than an
assumption: adapter identity by PCI bus and Vulkan UUID rather than DXGI index, media
engines classified by observed dominance rather than by name, rate control chosen on a
distribution across ten clips rather than on the worst one. Derek's rulings did most of the
narrowing — lane affinity not a global pause, capped mode not a raised alarm, `operator`
does not get `media_render`, ledger stays single-writer. Each of those closed a design
space I would have kept exploring.

The call that did **not** hold up is the one worth keeping. The architecture moved rendering
to the machine that holds the files and left reading on the machine that has to fetch them.
That was inherited, not chosen — but I built an entire cross-machine revision-authority
protocol on top of it without ever asking why the 4 GB was crossing at all. The copper link
was hiding the asymmetry, and I treated the link as a constant because it had always been
one. Derek named it in one sentence after the deploy failed.

**Change:** when a design's cost model rests on a specific piece of infrastructure, write
the dependency down as a premise with a number attached. `112 MB/s` in a comment would have
made the 4 MB/s measurement an obvious re-open rather than a surprise.

### Implementer — the code was fine; my verification of it was where the rework came from

Build quality held. The scheduler, promotion, and handoff modules landed close to final and
the tests written alongside them caught real regressions later. Almost all the rework in
this session came from *checking* rather than *building*: the QSV device-init string was
wrong and the CPU fallback hid it; my first supersede fix cancelled on any claim mismatch
including an unlabelled one, which would have killed live work on a guess; my OCR
equivalence proof ran against a silently-fallen-back CPU proxy rather than the QSV one I
believed I was testing.

The pattern is consistent and uncomfortable: **every fallback I wrote to be safe also made
a failure invisible.** The QSV fallback turned a broken command into a slow success. The
`if not exists` guards in the worker would have turned a stalled producer into a silent
16-minute read. Fallbacks need to be loud.

### Reviewer / QA — comparison caught what inspection could not

Two findings justify the whole session's testing posture, and neither was reachable by
reading code:

- **21.4 ms audio drift.** The commands were identical. The ffmpeg versions were not, and
  they disagree about whether a 0.021 s stream start offset is padded or trimmed. Found by
  decoding both outputs to raw PCM and diffing sample counts against AM4's own file.
- **h264_qsv NAL framing corruption.** Correct size, geometry, duration, `nb_frames`, clean
  `ffprobe` — and a third of the frames unreadable by a real decoder. Found only because
  the OCR frame count differed from a previous run and I chased the discrepancy instead of
  dismissing it.

What slipped: I shipped `build_result` without `clip_revision` for several commits, which
made AM4's staleness guard structurally dead — it read `None` and passed everything. A guard
with no test that proves it *fires* is decoration. The test suite grew from 1051 to 1078
partly to fix exactly that.

Test posture that worked: nothing mocked the filesystem. Every atomicity, marker-ordering
and supersede test operates on real files, because those failure modes only exist on disk.

### Operator / SRE — the deploy is where the design got audited

The gateway's session-0 blindness was found before it could bite: a scheduled task would
have started, reported healthy, and been unable to create a GPU device. That produced
ADR-0036 and the interactive-agent split.

The deploy itself surfaced the real incident. AM4 was crash-looping on `Errno 112 Host is
down: '/data/raw'` and had been since the cable moved — nothing was watching that, and the
first thing to notice was a deployment attempt. Then a second-order lesson: the
`configure-omen-mounts.sh` heredoc is quoted, so my first re-point would have written a
literal `${OMEN_HOST}` into fstab and broken every mount at next boot. Config-as-code that
nobody re-runs drifts silently from the machine.

I stopped at the blocker rather than guessing an address, and that was right — but I should
have measured the wireless link *before* proposing "re-point the mounts" as an option at
all. I offered it, then measured it, then withdrew it.

### Product / planning — the scope discipline was Derek's, and it worked

Derek's closure-mode message ("fix only these concrete gaps... new non-blocking findings go
into a backlog") is the reason this session finished instead of sprawling. The backlog file
absorbed five real findings without a single one turning into a build. When the copper
disruption hit, his instruction was equally bounded: producer-local extraction only, prove
one session, deploy, stop — explicitly *not* the broader topology redesign that the
situation invited and that I would have been tempted by.

The pacing failure was mine and Derek named it: twelve hours refactoring a plan that already
worked, to use a faster computer where the files already were. He was right, and the fair
version is that most of the render-lane work survives the topology change — it is the
cross-machine handoff, built specifically because AM4 was remote, that the change undercut.

### Two seats, two views

**From Claude's seat.** I am good at instrumenting a stated design and bad at questioning
the frame it arrives in. Every hard finding this session came from a comparison I ran; every
miss came from a premise I accepted. I inherited "AM4 reads raw over SMB" as a given and
built increasingly sophisticated machinery on top of it — atomic sidecars, exact revision
matching, promotion leases — none of which was wrong, all of which was serving a data flow
that should not have existed. The thing I'd want earliest next time is the cost model: what
crosses which boundary, how big, how often, and on what hardware. That one table would have
raised the question on day one.

I also over-trusted my own green results twice in one session, in the same shape both times:
a fallback fired, the run succeeded, and I recorded the success without checking which path
produced it.

**From Derek's seat** *(my reconstruction of his view — to be corrected).* The system works
and it took far too long to get there. The genuinely valuable output was not the render lane;
it was the two corruption findings and the measurements that pinned them, because those are
the kind of thing that would have quietly degraded highlights for months. The frustration
was legitimate and specific: the answer was visible from the start — the files are on OMEN,
OMEN is the faster machine, put the work there — and it took a cable being repurposed to
force it. He'd want less ceremony around a system whose whole job is to cut clips out of
game footage, and he'd want me to say "this data flow makes no sense" the first time I
touch it rather than the twelfth.

## Last time's lessons

| Lesson | Status | Evidence |
|---|---|---|
| L-2026-07-30-1 — a from-zero rebuild cannot detect its own staleness | acted-on (indirectly) | Same shape recurred and was caught: `validate_proxy` checks the artifact against an external expectation rather than trusting the producer's own success |
| L-2026-07-30-2 — a guard that is always red is a guard nobody reads | pending | Not exercised this session |
| L-2026-07-30-3 — never conclude from output I truncated myself | acted-on | Chased the 75-vs-118 frame discrepancy instead of dismissing it; that chase found the NAL corruption |
| L-2026-07-30-4 — if a handover needs pasted fragments, the handover is the defect | acted-on | Deploy went out as launcher `.cmd` files + registered tasks, not instructions |
| L-2026-07-30-5 — "installed" and "able to work" are different facts | **acted-on, twice** | Session-0 GPU blindness (starts, reports healthy, cannot create a device) and the QSV proxy (every metadata check passes, cannot be decoded) are both exactly this |
| L-2026-07-30-6 — local-model seat reads invent self-criticism, three retros running | **acted-on** | Seat reads written frontier this time; offloaded only the timeline condense and lessons extraction, which the lesson said stay usable. Draft graded `minor-fixes` |
| L-2026-07-30-7 — in-process `local_generate` calls are invisible to offload economics | pending | Unchanged; this retro's offload went through the door and does count |

## Lessons learned

1. **L-2026-08-25-1 — A premise with no number attached will be treated as a constant until
   it breaks.** "AM4 reads raw over SMB" was affordable only because of a 112 MB/s link that
   nobody had written down as a dependency. When the cable moved, the design did not
   degrade — its cost model inverted, 27x. *(→ ADR-0037; the premise now carries its
   measurement)*
2. **L-2026-08-25-2 — Every fallback that makes a failure survivable also makes it
   invisible; a fallback must be loud.** The QSV device string was malformed for an entire
   run and the CPU fallback turned it into a slower success, which then contaminated a
   downstream OCR proof. *(→ practice + memory)*
3. **L-2026-08-25-3 — Cheap checks certify containers, not content; anything downstream
   depends on must be verified by doing the thing.** The corrupt proxy passed size,
   geometry, duration, `nb_frames` and `ffprobe`, and lost a third of its frames to a real
   decoder. *(→ ADR-0037; `validate_proxy` decodes and counts)*
4. **L-2026-08-25-4 — A guard needs a test that proves it FIRES, not just that it exists.**
   `build_result` never wrote `clip_revision`, so AM4's staleness check read `None` and
   passed every result for several commits. The code looked right on both sides.
   *(→ practice)*
5. **L-2026-08-25-5 — When two machines run the same command, the command is not the
   contract — the output is.** Identical ffmpeg arguments produced a 21.4 ms A/V shift
   because the two versions disagree about padding a 0.021 s start offset. Only a
   sample-level diff against the incumbent's own output showed it. *(→ ADR-0037)*
6. **L-2026-08-25-6 — Cancel on proof, never on absence of proof.** My first supersede fix
   treated an unlabelled claim as stale and cancelled it; an unlabelled claim is evidence of
   nothing and may be the live job. Resubmitting was already safe via the idempotency key.
   *(→ practice)*
7. **L-2026-08-25-7 — Config-as-code that is never re-run drifts from the machine, and the
   drift surfaces at boot.** `configure-omen-mounts.sh` still hardcoded the retired address,
   and its quoted heredoc would have written a literal `${OMEN_HOST}` into fstab. *(→ doc;
   the script now parameterises the host)*
8. **L-2026-08-25-8 — Moving work to the right machine is only half the fix if the data flow
   stays put.** Rendering moved to where the files are; reading did not, so the largest
   transfer in the system was untouched by a project about transfers. *(→ ADR-0037)*
9. **L-2026-08-25-9 — Two subsystems sharing a hardware resource need the separation stated,
   not assumed.** Putting proxy encode on the iGPU to avoid the B70 media engines was right
   in intent, and the reason it was right only became explicit after it failed for an
   unrelated reason. Extraction is CPU, rendering is leased GPU. *(→ ADR-0037)*
10. **L-2026-08-25-10 — A rollback path that has not been exercised since the environment
    changed is not a rollback path.** `RENDER_BACKEND=am4` is still coded, still tested, and
    no longer operable — it reads the raw segment. Better to say so now than to find out
    during an incident. *(→ ADR-0037 Consequences + `DECISIONS-PENDING.md`)*

## Provenance

**Git ranges:** commandcenter `dac3138..58181ad` (17 commits, pushed to `origin/master`);
bf6-highlights `ec4766a..4a12a12` (8 commits, local-only repo — no remote).
Two commits in the commandcenter range (`34598f4`, `68c5bc8`, R&D session posture) are a
**concurrent session's**, not this one's. Working tree carries five pre-existing dirty files
(`CLAUDE.md`, two HTML dashboards, two `knowledge/*.json`) that Derek excluded from staging;
left untouched.

**Offloaded:** timeline condense + lessons extraction → `gcp-gemini` (`gemini-3.5-flash`),
1847 in / 1735 out, `routed_by: pinned:gcp-gemini`. **Edit verdict: `minor-fixes`** — the
timeline was faithful and needed only wording corrections (it said the cable was "reclaimed"
and that the proxy was "reverted to" libx264, which was never the prior state); several
drafted lessons were event-restatements rather than transferable claims and were rewritten
or dropped.

**Frontier:** all seat reads, both two-seat views, every lesson's final wording, ADR-0037,
and all repo-coherent writes. Seat reads were deliberately **not** offloaded — see
L-2026-07-30-6, this is that lesson's follow-through.

**`--fleet`:** not requested; no plan_ids were pending from 2026-07-30.

## Offload scorecard (S6)

`knowledge/offload.json` — watermark `2026-08-25T13:13:56Z`, refreshed during this retro
(it was stale at `2026-08-24T14:06Z`; this session's own retro call is the newest evidence
in it).

| | |
|---|---|
| Offload ratio | **1.00** |
| Calls | 1231 (sunk 631 · trial 590 · unknown 10) |
| Tokens out | 380,883 (sunk 159,099 · trial 221,784) |
| Est. saved vs `claude-sonnet` | **$14.83** |
| Real spent (Vertex) | $6.27 |

This session contributed one door call — the retro's timeline + lessons draft on
`gcp-gemini`, 1847 in / 1735 out. That is a small line, and honestly so: this was a session
whose work was measurement, hardware, and repo-coherent judgment, almost none of which is
offloadable. L-2026-07-30-7 still stands — the scorecard measures door traffic, and the
render lane's own ffmpeg dispatches are not offload events.
