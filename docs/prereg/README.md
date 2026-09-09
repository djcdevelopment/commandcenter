# Pre-registered experiments

Prediction cards committed **before** their data exists. The format is copied from
[denning](https://github.com/djcdevelopment/denning)'s `prereg/TEMPLATE-prediction-card.md`, and
so is the discipline — "the honesty engine": a refuted prediction is a success of the method, and a
prediction that was quietly edited after the fact is not a prediction.

**Rules**

1. A card is committed with its predictions, priors, exact protocol, pass gate, kill/pivot gate and
   pre-committed pivot **before the first cell runs**. That commit is the timestamp that matters.
2. The card is **tagged** (`prereg-<name>-<yyyymmdd>`) only after the predictions are final and Derek
   has acked the protocol. Until then the header says `NOT YET TAGGED`.
3. Results are **appended beneath** the predictions, each scored *supported* / *refuted* / *untested*
   against the criterion as written. The prediction text above the result is never edited; a correction
   is a new dated line, not a rewrite.
4. Instrument gaps discovered after commit are recorded in the card as `null` fields or `untested`
   scores — never by inferring the missing measurement from a different one.
5. Every number carries its regime (model, depth, concurrency, `-np`, placement), and
   `docs/CLAIM-REGISTER.md` is updated for anything a card's result corrects.

**Cards**

| card | tag | status |
|---|---|---|
| [SATURATION-SURFACE-LAP1.md](SATURATION-SURFACE-LAP1.md) — the B70 saturation surface under concurrent intake | `prereg-saturation-lap1-20260909` (pending ack) | predictions committed `b662991`; protocol facts corrected 2026-09-09 (bearer required on `:8082`; symmetry gate located; duty-cycle reference absent); no cell run |
