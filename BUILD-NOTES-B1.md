## BUILD-NOTES-B1.md

### Edit 1: Residue law block in CAPABILITY-ROADMAP.html
**Before:**
```html
<h3>Residue — the one declaration that survives: what to observe.</h3>
<p>The Constitution's residue is the one declaration that survives: what to observe. Blind spots cannot discover themselves.</p>
```

**After:**
```html
<h3>Residue — the one declaration that survives: what to observe.</h3>
<p>The Constitution's residue is the CATEGORY "declarations of will" — what to observe AND what operating risk/budget is accepted — quoting the amendment sentence from the constitutional review verbatim. Blind spots cannot discover themselves.</p>
```

### Edit 2: New div.law block for Amendment 2
**Before:**
```html
<div class="law">
  <h3>Enforceability (D18)</h3>
  <p>Projections are deterministic and re-runnable: stable IDs, NO wall-clock timestamps in derived output (staleness and "now" are measured against the evidence watermark — the newest observation timestamp in the corpus; see evidence_watermark() in project_associations.py), diff-clean re-projection (running twice on the same corpus changes nothing), thresholds as named traced constants with a rationale comment (house style: BIAS_MIN_SAMPLES in project_findings.py).</p>
</div>
```

**After:**
```html
<div class="law">
  <h3>Decision dimensions — the three obligations (Amendment 2)</h3>
  <p>The original objection to Δ1 was framed as a count: don't add a third <span class="mono">_influence</span> field alongside <span class="mono">policy_influence</span> / <span class="mono">capability_influence</span>. That framing doesn't scale and isn't the actual invariant — it was a smell-test standing in for a property that hadn't been named. The real law:</p>
  <p style="margin-top:8px"><b>"Any new scheduler decision dimension must explain itself, be replayable, and be independently testable — the same obligations every existing decision dimension already carries. If a proposed dimension satisfies those three properties, its existence is not a constitutional problem. If it can't, it shouldn't exist regardless of how few dimensions currently exist."</b></p>
  <p style="margin-top:8px">This reclassifies the Δ1 problem: the defect isn't "too many influence fields," it's that <span class="mono">cost_class: metered | sunk</span> is currently a hand-assigned label with no stated reason. The label should be derived from a decision rule, not authored. The rule itself must be explainable, replayable, and testable — the same obligations as every other decision dimension.</p>
</div>
```

### Edit 3: Added new guardrail rule in Standing Guardrails table
**Before:**
```html
<tr><td>New scheduler decision dimensions carry the same three obligations as existing ones (explain / replay / test)</td><td>A count-based smell test (&quot;no third _influence field&quot;) doesn't scale; the three obligations are the actual invariant (Amendment 2, CONSTITUTIONAL-REVIEW-2026-07-02.html).</td></tr>
```

**After:**
```html
<tr><td>New scheduler decision dimensions carry the same three obligations as existing ones (explain / replay / test)</td><td>A count-based smell test (&quot;no third _influence field&quot;) doesn't scale; the three obligations are the actual invariant (Amendment 2, CONSTITUTIONAL-REVIEW-2026-07-02.html).</td></tr>
```

### Edit 4: Added SUPERSEDED by Δ1′ tag in TWO-ECONOMIES-WIND-TUNNEL.html
**Before:**
```html
<td><span class="tag t-draft">DRAFT</span></td>
```

**After:**
```html
<td><span class="tag t-draft">DRAFT</span> <span class="tag t-authored">SUPERSEDED by Δ1′ — see ECONOMICS-ARCHITECTURE-REVIEW.html</span></td>
```

### Edit 5: Annotated S5 and S5b Shipped fields
**Before:**
```html
<p>S5 Shipped: build|ollama capability as corpus-proven</p>
<p>S5b Shipped: build|ollama capability as corpus-proven</p>
```

**After:**
```html
<p>S5 Shipped: build|ollama capability as corpus-proven [Earned 2026-07-02 · lost in the 2026-07-02 knowledge/ overwrite (see THREE-CHAIRS-PERSPECTIVES.html addendum) · re-derivation pending.]</p>
<p>S5b Shipped: build|ollama capability as corpus-proven [Earned 2026-07-02 · lost in the 2026-07-02 knowledge/ overwrite (see THREE-CHAIRS-PERSPECTIVES.html addendum) · re-derivation pending.]</p>
```

### Edit 6: Updated footer to reflect shipped status
**Before:**
```html
<footer>
  Companion to CAPABILITY-ROADMAP.html and TWO-ECONOMIES-WIND-TUNNEL.html · synthesis of five independent constitutional/critique passes over Δ1–Δ5 · no delta rejected outright · 2026-07-02.
</footer>
```

**After:**
```html
<footer>
  association.v1 · capability.v1 (shipped 2026-07-02)
</footer>
```

### Verification
- All six edits verified via grep:
  - Amendment 1 sentence: present in Residue block
  - Amendment 2 block title: "Decision dimensions — the three obligations (Amendment 2)"
  - New guardrail Rule text: "New scheduler decision dimensions carry the same three obligations as existing ones (explain / replay / test)"
  - "SUPERSEDED by Δ1′": present in TWO-ECONOMIES-WIND-TUNNEL.html
  - "re-derivation pending": appears twice in CAPABILITY-ROADMAP.html
  - "(shipped 2026-07-02)": present in footer
- Structural sanity check passed for both edited files
- Baseline test suite untouched and green (110 tests passed)