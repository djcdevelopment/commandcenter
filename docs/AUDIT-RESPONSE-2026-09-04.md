# Response to the HEARTH network & control-plane audit (Gemini Flash 3.8, 2026-09-04)

Source: `C:\Users\derek\.gemini\antigravity\brain\ebbae9bb-…\hearth-audit-recommendations.md`.

**Verdict: accurate.** Every claim I independently checked held up — 8 of 8 network probes, and both
P0 code findings confirmed in the source. Two of its findings I had *observed and failed to chase*
during the same session, which is the useful part: it caught things I was looking straight at.

Standing rule applies (`feedback-verify-relayed-agent-reports`): what follows separates **verified**
from **taken on trust**. Nothing was acted on before verification.

## Verified in source, and FIXED this session

### P0-1 — `doorcheck` reported `backend_dependency: cold` permanently ✅ FIXED

Confirmed exactly as described. `hearth/callers/doorcheck.py`: for `api == "openai"`,
`entry["up"] = None` with the comment *"am4-oxen: TCP-only, informational — AM4 sleeps by design
(banked fire)"*; then `default_up = bool(entry.get("up"))`, and `bool(None)` is `False`.

The branch was correct when the only openai rung was `am4-oxen`, a banked-fire node **meant** to be
asleep. ADR-0034 then made `omen-arc` (`api = "openai"`) the pool default, and the health gate has
been stuck red ever since.

**I saw this myself, repeatedly, and did not chase it** — every `doorcheck` run in this session
printed `facet[backend_dependency] : cold` while `omen-arc` served at 107 tok/s `at_rate`. That is
the audit's own "Impact" line landing on me: a permanently-red gate trains its reader to ignore it.

Fixed by falling back to the `awake` probe when `up` is not a real boolean. **Verified live:**
`facet[backend_dependency] : healthy`. The fix carries a caveat in the code, because it matters:
`awake` means the **port answers**, not that the model can emit a token — `am4-oxen` answered on
`:8090` for days with every model `ready:false`. `query_rung_state` (ADR-0044) remains the only
verdict that requires a deep sample.

### P0-2 — a missing local token silently escalated to a paid rung ✅ FIXED (differently)

Confirmed, and the mechanism is worth stating precisely because my own notes said the router
"fails closed":

- A **pinned** call with no token errors cleanly — `inference.py:362`, pins never escalate. That is
  the behaviour my notes described, and it is fine.
- A **non-pinned** call routes to the default (`omen-arc`), fails auth, and then **A2 ladder
  escalation** (`inference.py:604`) climbs one rung — to `gcp-gemini`, spending trial credit to
  cover an unset env var in the caller's shell.

The audit recommends an ops workaround (inject `OMEN_ARC_TOKEN` into the profile). **I fixed the
code instead**, which seems more in keeping with the no-silent-fallbacks doctrine: the no-auth
result now carries `error_code: "auth_not_configured"`, and escalation refuses to climb on it. A
missing token is a fault in *this shell's environment*, not a statement about the rung's capacity —
the rung is fine, we simply cannot address it. Failing loudly on the named rung is the honest
outcome. The ops fix is still worth doing; it is now belt-and-braces rather than the only guard.

**A detail that vindicates the finding:** `test_connection_failure_returns_result_not_exception` in
`test_inference.py` had been **passing because of the leak** — it only reached its expected
`URLError` by escalating past the auth failure to a second rung. The test now supplies a token so
the connection is what fails, and a new test asserts no climb occurs on an auth fault.

## Verified by probe (8 of 8 matched the audit)

| claim | probed | result |
|---|---|---|
| AM4 Samba `:445` DOWN | `192.168.12.233:445` | **DOWN** ✓ |
| FX99 Samba `:445` DOWN | `192.168.12.220:445` | **DOWN** ✓ |
| AM4 oxen facade `:8090` UP (empty) | `192.168.12.233:8090` | **UP** ✓ |
| FX99 Ollama `:11434` UP | `192.168.12.220:11434` | **UP** ✓ |
| AM4 SSH UP | `192.168.12.233:22` | **UP** ✓ |
| OMEN `:11435` tracing proxy UP | `127.0.0.1:11435` | **UP** ✓ |
| …its backing Ollama `:11434` retired | `127.0.0.1:11434` | **DOWN** ✓ (ghost proxy confirmed) |
| OMEN `:8711` Caddy UP | `127.0.0.1:8711` | **UP** ✓ |

## Taken on trust — NOT verified here

State these as the audit's claims, not as findings, until someone checks them:

- **Finding 3** (Hyper-V Default Switch prefix churn breaking `cc-conductor` SSH host keys). Plausible
  and consistent with `project-fleet-vm-provisioning`, but I ran no SSH from `cc-conductor`.
- **Finding 4 root causes** — I confirmed `:445` is down on both hosts; I did **not** confirm *why*
  (`ufw` rule vs `smbd` binding). The recommended `ufw allow in on wlp4s0 … port 445` is a guess at
  the cause, and a reasonable one, but it is a guess.
- **`claudefarm1` rejecting `cc-conductor`'s key**, and the `172.17.192.0/20` prefix.
- **Finding 2 (phantom limbs)** — the file/line citations look right and match
  `reference-am4-b70-cards` (cards moved to OMEN 2026-08-20), but I did not read
  `summon.py` / `dream.py` / `am4.py` in this pass.

## Not done, and why

- **P1 SSH config, P1 Samba, P2 `claudefarm1` keys** — these are changes on *other machines* (AM4,
  FX99, `cc-conductor`), not in this repo. They need Derek or a session on those hosts.
- **P2 phantom limbs** (`wake_am4`, `dream`, `am4.py`, `occupancy.MOE_SLOTS_URL`) — a real cleanup
  and correctly diagnosed, but it touches the imagegen lane's territory and deserves its own pass
  rather than being folded into a session about rotation. Registered.

## One caution about the audit itself

Its network matrix is a **single-sample snapshot**, and the same trap that bit me twice today applies
to it: a port that answers is not a service that works (`:8090` and `:11435` are both in the table as
UP, and both are hollow — the audit says so, to its credit). Read the "Live State" column as
*reachability*, never as capacity.
