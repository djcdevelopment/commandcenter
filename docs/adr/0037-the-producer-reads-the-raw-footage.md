# 0037 — The producer reads the raw footage: extraction runs where the bytes already are

**Status:** Accepted (2026-08-25); deployed and proven on a real session the same day

**Companion to:** `docs/adr#0035` (rendering is its own authority), `docs/adr#0036` (GPU
execution leaves the control plane)

## Context

ADR-0034 moved BF6 rendering onto the two Arc Pro B70s in OMEN, and ADR-0036 split the GPU
work into an interactive-session agent. Both were correct and both left one thing alone:
**AM4 was still the machine that opened the raw video.**

Its analysis path built a 720p proxy for OCR and pulled three audio tracks for Whisper, and
every byte of that came from OMEN's `E:` across an SMB mount. Roughly 4 GB crossed the wire
per segment so that ~100 MB of derived artifacts could come back. On the 10.44.0.0/30 copper
link, measured at 112 MB/s, that read cost ~33 s and nobody had reason to look at it.

On 2026-08-25 the cable was needed elsewhere. AM4 had already moved to wireless
(`dac3138`), and the mounts pointed at an address that no longer existed on either end —
OMEN's `Ethernet 4` reads Disconnected, AM4 has no `10.44.0.x`. The worker crash-looped on
`OSError: [Errno 112] Host is down: '/data/raw'`.

The replacement path was measured, not assumed: **~4 MB/s** OMEN → AM4 over the LAN. The
same 3.94 GB segment read goes from ~33 s to **~16 minutes**, and a 17-segment session from
~9 minutes of transfer to roughly four and a half hours. Re-pointing the mounts would have
been a 27x regression dressed as a repair.

The uncomfortable part is that the topology change did not create this flaw, it only
exposed it. Rendering had been moved to the machine holding the files while reading stayed
on the machine that had to fetch them. The link had been hiding the asymmetry.

## Decision

**The machine that holds the bytes does the reading. AM4 never opens raw video.**

A producer on OMEN (`omen-extractor/extract.py`) watches for closed segments and writes the
derived artifacts — `proxy.mp4`, `derek.wav`, `discord.wav`, `game.wav`, `probe.json` —
into `work/<session>/<segment>/`, which is exactly where the worker already looked. Measured
on a real segment: **97.9 MB instead of 3.94 GB, a 40x reduction**; wire time ~984 s → ~24 s.

Four properties make it safe rather than merely smaller:

1. **The marker is written last.** Every artifact is written to `.tmp` and renamed; the
   readiness marker is published only after all of them. AM4 keys off that marker alone, so
   it cannot observe a half-written wav, and a crash mid-extraction leaves no marker.
2. **The refusal is structural.** Under `EXTRACT_SOURCE=omen` the worker's `create_proxy`
   and `extract_audio` raise, and a missing probe is an error. The old code would simply
   have produced the artifact itself — correct, and silently sixteen minutes slow. Relying
   on "the file happens to be there" would have made the guarantee an accident.
3. **An unextracted segment is left unclaimed, not failed.** Arriving before the producer
   finishes is normal. Claiming would burn an attempt and, past the cap, park good footage
   as `failed_permanent` for being early.
4. **The producer validates what it publishes.** A full decode and frame count before the
   marker — see the encoder decision below for why this is not optional.

### The proxy is encoded on the CPU

The obvious choice was QSV on the Arrow Lake iGPU: hardware encode, and deliberately not on
a B70, whose media engines are the render scheduler's resource. It was faster (58 s vs
75 s) and passed every cheap check — correct size, geometry, duration, `nb_frames`, and a
clean `ffprobe`.

It was also corrupt. A real decode hit `Invalid NAL unit size` and `Error splitting the
input into NAL units` throughout, and `fps=1/2` sampled **75 frames where the CPU encode
gives 118**. A third of the OCR samples, gone, with no error anywhere — fewer highlights
found, silently.

So the proxy is `libx264` on the 285K's idle cores, at ~17 s more per segment. `--qsv`
survives for re-testing after a driver update, and `validate_proxy` rejects its output while
the framing bug persists.

### The audio start offset is pinned

The audio commands are the worker's, with one addition. These OBS tracks start at 0.021 s.
AM4's ffmpeg pads that offset with silence; OMEN's 8.1.2 trims it, moving every sample
**342 samples (21.4 ms) earlier** against the same video frames. Transcript timestamps
become caption timings and clip cut points, so that drift would have been burned into every
rendered clip. `aresample=async=1:first_pts=0` closes it to 6 samples (0.375 ms).

## Consequences

**`RENDER_BACKEND=am4` is no longer a working fallback.** A local render reads the raw
segment, which is the thing that moved. The flag still exists and the code path is
untouched, but exercising it now means ~16 minutes of transfer per clip. Rollback is
`git revert` **plus the cable**, not a flag flip. This is the real cost of the decision and
it should not be discovered during an incident.

**The two-release strategy lost its safety net.** Release A was supposed to prove the
control plane on `am4` before flipping to `omen`. With copper gone that proof is not
available, so both planes were deployed together — a deliberate departure, forced by the
topology, not a shortcut.

**AM4's remaining SMB traffic is small and its mounts are on the LAN.** Mounts re-pointed
`10.44.0.1` → `192.168.12.239`; SMB was already reachable there, so this changed the address
and not the exposure. `infra/am4/configure-omen-mounts.sh` was re-pointed too — its heredoc
is quoted, so a bare `$OMEN_HOST` would have been written into fstab literally and every
mount would have failed at boot.

**Extraction and rendering cannot contend.** Extraction is CPU; rendering is the B70 media
engines under lease. That separation is now load-bearing rather than incidental, and the
QSV-on-iGPU experiment is what proved it needs to be stated.

## Alternatives considered

**Re-point the mounts and carry on.** Rejected on measurement: 27x slower on the path that
matters, and it would have preserved the asymmetry that caused the problem.

**Move analysis to OMEN entirely**, leaving AM4 with only the review UI and database.
Cleaner, and it would make the sidecar bridge largely unnecessary — but it is a topology
redesign, it strands the RTX 5070 that does Whisper well, and it was explicitly out of
scope. Recorded here because it remains the smaller system if AM4's role shrinks again.

**Ship audio only and skip the proxy.** Rejected: `ocr_signals` reads the proxy, so the
analysis would lose its kill-feed detection.

## Evidence

- Wire measurement: 300 MB OMEN → AM4 in 74 s (~4 MB/s), against 112 MB/s on the retired copper.
- Extraction on a real 3.94 GB segment: 97.9 MB produced, 87.7 s including validation.
- End-to-end: AM4 picked the segment up 10 s after the marker with no raw read; clip reached
  `draft` on `b70@bus4`; both variants 3840x2160 / 1080x1920 at 60 fps, 180.000 s, audio
  present; no `.part` or orphaned sidecars.
- OCR equivalence on the shipped (libx264) proxy: 32 signals vs NVENC's 25, 96% of NVENC's
  detections preserved.
