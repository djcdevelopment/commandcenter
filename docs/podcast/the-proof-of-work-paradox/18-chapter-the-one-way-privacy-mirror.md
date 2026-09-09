# The HEARTH Wire: The Proof of Work Paradox
## Chapter 18: The One-Way Privacy Mirror

**Setting:** A cool, crystalline chime rings out. The music adopts a clean, transparent, architectural quality, like looking through two-way glass.

---

**ALEX:**
Sam, let’s talk about security and privacy. Because Derek is doing something that very few AI developers dare to do: he is taking the internal audit ledger of his personal engineering lab and publishing live aggregate telemetry directly onto his public corporate website at `steppeintegrations.com`. How do you publish proof of work without accidentally leaking API keys, client code, file paths, or private prompts to the entire internet?

**SAM:**
You build a one-way privacy mirror. That’s what [`hearth/projection/public_portfolio.py`](file:///c:/work/commandcenter/hearth/projection/public_portfolio.py) is.
Look at the header documentation of that file:
> *"Export a content-free, privacy-gated proof snapshot for steppeintegrations.com. The private ledgers contain prompt previews, paths, identities, exact timestamps, and error details. None of those values are copied or pseudonymized here. This projection emits only fixed-dimension counts, day-granularity windows, coarse MechNet state, and hashes of the consumed append-only prefixes."*

**ALEX:**
Look at the safeguards built into that Python module:
First, **Forbidden Source Keys**:
`args_preview`, `caller`, `task_id`, `event_id`, `error`, `hostname`, `port`, `path`, `prompt`, `request_id`, `job_id`, `principal`.
If *any* of those keys appear anywhere in the output JSON, the validator throws `PublicProjectionError` and refuses to emit the snapshot!

**SAM:**
Second, **Forbidden Text Regular Expressions**:
Lines 181 through 187 compile regexes searching for Windows drive letters (`C:\`), user directory paths (`\Users\`), IP addresses, GUIDs, and email addresses. If even a single IP address or file path slips through into the serialized candidate, the projection aborts fail-closed.

**ALEX:**
And third, the **Minimum Public Cell Rule**:
`MINIMUM_PUBLIC_CELL = 10`.
Explain what that does.

**SAM:**
Differential privacy. If a weekly aggregate cell has fewer than ten observations—say, four test runs or two git commits in a single week—that number is suppressed and masked to `null`. Why? Because small numbers allow outside observers to correlate timestamps and infer specific private activities.

**ALEX:**
And how does the public verify that the numbers weren't just made up in Photoshop or typed into a JSON file by hand?

**SAM:**
Through the cryptographic provenance block:
```json
"provenance": {
  "exporter_revision": "e9822d32...",
  "exporter_sha256": "26696693...",
  "gateway_prefix_sha256": "424d4848...",
  "execution_prefix_sha256": "eb2da38a..."
}
```
The exporter takes the raw append-only files on disk and computes the SHA-256 hash of the consumed prefix.
Then it takes the entire public JSON payload, canonicalizes the keys, strips whitespace, and hashes it to produce the `snapshot_id`:
`sha256:5988cc1e180e258d...`

**ALEX:**
It’s a merkle root! If you change even one single digit—if you change an inference count from 867 to 868—the hash breaks, the signature fails schema validation, and the deployment is rejected.
It is an airtight privacy barrier.
But when you look at that verified public mirror, there was one category that looked surprisingly swollen: a mysterious bucket called "Other" that held over 2,700 events. In Chapter Nineteen, we investigate the Unclassified Overflow.
