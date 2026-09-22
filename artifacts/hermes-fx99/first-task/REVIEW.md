# Independent review — Codex

Hermes completed the raw source note in 99.966 seconds (session
20260919_171008_992f00); no assisted export/recovery was required. It made eight
API calls. Session-reported usage is preserved separately; per-attempt importer
was not active for this initial note, so these aggregates must not be presented
as imported physical-attempt receipts.

Useful and grounded: the inventory is stale, AM4's second GPU is missing, and
aliases/consumer roles do not create physical slots. Historical rates must not
be used as current measurements. Hermes explicitly disclosed that its catalog
read was blocked by HEARTH's existing knowledge-read guard.

Corrections before implementation:

- Keep the authenticated client facade at :8090; :18090 is its engine upstream.
- The GPU catalog is authored configuration. It can be changed through a
  reviewed source candidate with evidence; it must not be confused with derived
  findings/capacity projections or rebuilt merely to bypass the read guard.
- Check the catalog's `cards`, not a nonexistent `gpus` field. Inventory uses
  `gpus`; the two schema names differ.

Next task is actual source + regression tests through the existing conductor.
