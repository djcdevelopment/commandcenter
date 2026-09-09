# The HEARTH Wire: The Proof of Work Paradox
## Chapter 24: The Cryptographic Session Seal

**Setting:** A solemn, ceremonial sound—like an embossing press stamping hot red wax onto an official archival deed. A dignified, cinematic score begins to swell.

---

**ALEX:**
Sam, in historical craftsmanship, when a master watchmaker, a blacksmith, or an engraver finished an important commission, they didn't just hand it over. They struck their maker's mark into the steel. They sealed the letter with hot wax and a personal signet ring.
How do we bring that maker's mark into the digital world of autonomous engineering?

**SAM:**
We build the **Cryptographic Session Seal**: `hearth seal`.
At the end of an engineering session—after you’ve written code, passed tests, updated ADRs, and made commits—you open your terminal and type:
`hearth seal`
And here is what that ceremony does:
1. It queries the local Git repositories to see every commit created during the session.
2. It hashes the working tree and changed files to produce a cryptographic workspace digest.
3. It gathers the execution receipts from the local gateway—how many tokens were burned, how many assays were run, how many models were rotated.
4. It submits an explicit **Execution Job** through the ADR-0030 execution control plane: `req_...` → `job_...`.
5. The execution ledger writes an immutable `artifact.recorded` event, content-addressed with a SHA-256 digest, containing the complete session proof.

**ALEX:**
And what does it print to the console when it finishes?

**SAM:**
It prints an official **Proof Receipt**:
```text
════════════════════════════════════════════════════════════════
                HEARTH SESSION PROOF RECEIPT
════════════════════════════════════════════════════════════════
Session ID:       sess_20260906T234500Z_derek
Author:           Lead Engineer (Derek Ciula)
Host Hardware:    OMEN (2 × Intel Arc Pro B70 · Vulkan)
Commits Anchored: 4 commits (e9822d32..ccc36a7c)
Assays Verified:  24 tests green (0 regressions)
Inference Used:   14,280 tokens (omen-arc resident)
Artifact Digest:  sha256:7f259724ef970f67e4a9d2eaa4f9f24f8d...
Ledger Sequence:  19623 (Execution Ledger)
Watermark Day:    2026-09-06
════════════════════════════════════════════════════════════════
Status: IMMUTABLY SEALED TO APPEND-ONLY RECORD
```

**ALEX:**
Look at that receipt.
It links the human author, the physical hardware, the git commits, the unit tests, the model tokens, the exact ledger sequence number, and the cryptographic SHA-256 hash into a single, content-addressed artifact!

**SAM:**
And because it’s recorded as an execution artifact in `hearth/var/execution/events.ndjson`, the next time the nightly staging script runs:
`powershell -File hearth\etc\stage-public-portfolio.ps1`
That session receipt becomes part of `execution_prefix_sha256`!
It is mathematically rolled into the root hash that is published to `steppeintegrations.com`!

**ALEX:**
You cannot forge that. You cannot retroactively fabricate it. You cannot Photoshop it.
If someone wants to verify that Derek Ciula actually wrote the code, tuned the kernels, and ran the tests on that exact day on that exact machine, the proof is permanently sealed in the public record.

**SAM:**
And that leads us to the final chapter of our series. Because once the receipts are sealed in the ledger, how do we give the outside world the power to verify them for themselves?
In Chapter Twenty-Five, we step into the future: The Verified Frontier.
