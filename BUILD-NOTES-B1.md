# BUILD-NOTES-B1 — constitutional HTML amendments

Curator pass (landed locally from the approved STREAM B1 prompt; the fleet's `pour-b1`
winner rewrote the derived `knowledge/*.json` destructively instead of applying these six
scoped edits, so it was used only as a signal that the stream needed a human pass).

Scope: authored HTML narrative only. No code, schema, or `knowledge/` changes. Each file's
CSS and structure preserved; no content changes beyond the six edits below.

## The six edits

### 1. CAPABILITY-ROADMAP.html — Residue law block rewritten (Amendment 1)
The residue is now named as a **category** ("declarations of will"), not a single "what to
observe" exception, quoting the constitutional-review sentence verbatim. Original "Blind
spots cannot discover themselves." sentence kept.

- Before: `<h3>Residue — the one declaration that survives: what to observe.</h3>` … "The instrumentation decision (“observe this”) remains a human, authored act …"
- After: `<h3>Residue — the category that survives: declarations of will.</h3>` … **"The irreducible authored residue consists only of declarations of will: what the organization chooses to observe, and what operating risk or budget it accepts. Everything else is projected from evidence."** … "Both the instrumentation decision … and the operating budget … remain human, authored acts …"

### 2. CAPABILITY-ROADMAP.html — new law block (Amendment 2)
Inserted a new `div.law` immediately **after** the Enforceability (D18) block and before the
Residue block.

- After: `<h3>Decision dimensions — the three obligations (Amendment 2).</h3>` with the Amendment 2 sentence verbatim ("… must explain itself, be replayable, and be independently testable …").

### 3. CAPABILITY-ROADMAP.html — Standing Guardrails table, one row added
Appended as the last row before `</table>`.

- Rule: `New scheduler decision dimensions carry the same three obligations as existing ones (explain / replay / test)`
- Why: `A count-based smell test (&quot;no third _influence field&quot;) doesn't scale; the three obligations are the actual invariant (Amendment 2, CONSTITUTIONAL-REVIEW-2026-07-02.html).`

### 4. TWO-ECONOMIES-WIND-TUNNEL.html — Δ1 stage card header
Added a second tag beside the existing DRAFT tag, reusing the existing `t-authored` class (no
new CSS). Original Δ1 content left fully visible beneath — audit trail, not erasure.

- Before: `…<span class="tag t-draft">DRAFT</span></div>`
- After: `…<span class="tag t-draft">DRAFT</span><span class="tag t-authored">SUPERSEDED by Δ1′ — see ECONOMICS-ARCHITECTURE-REVIEW.html</span></div>`

### 5. CAPABILITY-ROADMAP.html — S5 and S5b "Shipped" fields annotated
Appended, inside each existing `<p>`, the same sentence (twice total). Original claims kept.

- Appended: ` [Earned 2026-07-02 · lost in the 2026-07-02 knowledge/ overwrite (see THREE-CHAIRS-PERSPECTIVES.html addendum) · re-derivation pending.]`

### 6. CAPABILITY-ROADMAP.html — footer status
- Before: `→ <span class="mono">association.v1 · capability.v1</span> (next) · 2026-07-02`
- After: `· <span class="mono">association.v1 · capability.v1</span> (shipped 2026-07-02) · 2026-07-02`

## Verification

- Grep (one match each, `re-derivation pending` = 2): all pass.
- Structural sanity: `python -c "from html.parser import HTMLParser; HTMLParser().feed(open('<FILE>',encoding='utf-8').read())"` → clean for both files.
- Baseline suite untouched: `python -m unittest discover -s tests/workflow` → `Ran 130 tests … OK`.

## For a reviewer

- All quoted amendment sentences are verbatim from `CONSTITUTIONAL-REVIEW-2026-07-02.html`
  (Amendment 1 ~line 103-105, Amendment 2 ~line 116-119).
- Edit 6 intentionally leaves the trailing doc-date `· 2026-07-02` in place; only the
  `→ … (next)` substring was changed, per the stream's exact target string.
- Out of scope (unchanged): schema/code; THREE-CHAIRS-PERSPECTIVES.html and the review docs.
