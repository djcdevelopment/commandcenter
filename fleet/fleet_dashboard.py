#!/usr/bin/env python3
"""
fleet-dashboard — the OMEN-hosted fleet viewport (Scrum board, operator view).

Runs fleet_ping's reachability sweep over fleet/inventory.toml and renders it as
one static, self-contained HTML page (inline CSS, no JS, no dependencies) at the
repo root — FLEET-DASHBOARD.html — the same shape as HEARTH-DASHBOARD.html. It
does not depend on the conductor: it survives the conductor going dark, which is
exactly when the operator most needs to know what is reachable.

    python -m fleet.fleet_dashboard                       # sweep + write FLEET-DASHBOARD.html
    python -m fleet.fleet_dashboard --all-services        # probe EVERY declared service
    python -m fleet.fleet_dashboard --timeout 2
    python -m fleet.fleet_dashboard --out some/other.html
    python -m fleet.fleet_dashboard --json-out sweep.json # also keep the raw feed
    python -m fleet.fleet_dashboard --no-rung-state       # skip the omen-arc rate-health read

The page carries an optional RUNG-STATE block: when ``hearth.health.rungstate``
is importable, the omen-arc verdict (ADR-0044: baseline epoch + observed rate +
envelope) is read passively from the keep-alive tail and shown beside the
liveness table — a port that answers a TCP connect is UP to the sweep and may
still be DEGRADED here; that gap is the point of showing both. The read is
best-effort: if the module is missing or raises, the page still renders.

Exit code: 0 once the page is written (the reachability *gate* stays with
fleet_ping's exit code); 2 on an unreadable inventory.

Stdlib only. Never modifies the fleet — every probe is a read-only TCP connect.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Repo root so `fleet.*` / `hearth.*` import whether run as a module or a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fleet.fleet_ping import (  # noqa: E402
    DEFAULT_INVENTORY, DEFAULT_TIMEOUT, load_inventory, probe, summarize, sweep,
)

DEFAULT_OUT = _REPO_ROOT / "FLEET-DASHBOARD.html"
DEFAULT_RUNG = "omen-arc"
PAGE_TITLE = "Fleet Dashboard"

# status key (fleet_ping.classify) -> (label, css class)
_STATUS = {
    "up":      ("UP", "good"),
    "up-opt":  ("up (optional)", "good"),
    "down":    ("DOWN", "bad"),
    "offline": ("offline", "muted"),
}

# rung verdict (hearth.health.rungstate) -> css class
_VERDICT_CLASS = {
    "at_rate": "good",
    "warn": "warn",
    "stale": "warn",
    "degraded": "bad",
    "stalled": "bad",
    "unreachable": "bad",
    "no_baseline": "muted",
    "unknown": "muted",
}

_CSS = """
:root {
  --bg: #faf8f4; --fg: #22211e; --muted: #6b6760; --accent: #b4541e;
  --card: #ffffff; --border: #e2ddd3; --good: #2e7d32; --warn: #b26a00; --bad: #c62828; --code-bg: #f0ece4;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1c1b18; --fg: #e8e4dc; --muted: #a09a8e; --accent: #e8925a;
    --card: #262420; --border: #3a372f; --good: #81c784; --warn: #ffb74d; --bad: #ef9a9a; --code-bg: #2e2b25;
  }
}
body { background: var(--bg); color: var(--fg); font-family: system-ui, sans-serif; padding: 20px; line-height: 1.5; margin: 0; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 15px; margin-bottom: 20px; }
.tiles { display: flex; gap: 20px; flex-wrap: wrap; }
.tile { flex: 1; min-width: 150px; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.9em; }
th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: normal; }
tr.sub td { padding-top: 2px; padding-bottom: 6px; border-bottom: none; color: var(--muted); font-size: 0.92em; }
tr.sub + tr td { border-top: 1px solid var(--border); }
.metric { font-size: 2em; margin-top: 8px; font-weight: 500; }
.good { color: var(--good); } .warn { color: var(--warn); } .bad { color: var(--bad); } .muted { color: var(--muted); }
.pill { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 0.85em; font-weight: 600; border: 1px solid currentColor; white-space: nowrap; }
code { background: var(--code-bg); padding: 1px 4px; border-radius: 3px; font-size: 0.92em; }
h1, h2, h3 { margin-top: 0; font-weight: 500; }
h3 { color: var(--muted); font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.5px; }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; font-size: 0.92em; }
.kv dt { color: var(--muted); } .kv dd { margin: 0; }
.wrap { overflow-x: auto; }
"""


# --- pure helpers (unit-tested) -----------------------------------------------

def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _fmt_generated(generated_at) -> str:
    """datetime (naive = UTC) or any string -> 'YYYY-MM-DD HH:MM:SS UTC' / the string."""
    if isinstance(generated_at, datetime):
        dt = generated_at if generated_at.tzinfo else generated_at.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(generated_at)


def _fmt_ms(ms) -> str:
    return f"{ms:.0f} ms" if isinstance(ms, (int, float)) else "-"


def _normalize_sweep(sweep_doc) -> tuple[dict, list]:
    """Accept fleet_ping's --json shape ({summary, nodes}) or a bare row list."""
    if isinstance(sweep_doc, dict):
        rows = list(sweep_doc.get("nodes") or [])
        summary = sweep_doc.get("summary") or summarize(rows)
    else:
        rows = list(sweep_doc or [])
        summary = summarize(rows)
    return summary, rows


def _render_rung_state(state) -> str:
    """The optional ADR-0044 block. ``state`` None -> 'unavailable' line."""
    parts = ['<div class="card">', "<h2>Rung state (ADR-0044)</h2>"]
    if not isinstance(state, dict):
        parts.append('<div class="muted">Rung state unavailable — '
                     "<code>hearth.health.rungstate</code> not importable from this checkout, "
                     "or the read failed. Liveness above still stands; rate health is unknown.</div>")
        parts.append("</div>")
        return "\n".join(parts)

    verdict = str(state.get("verdict", "unknown"))
    cls = _VERDICT_CLASS.get(verdict, "muted")
    obs = state.get("observed_tok_s")
    base = state.get("baseline_tok_s")
    frac = state.get("frac_of_baseline")
    env = state.get("envelope") or {}
    parts.append('<div class="tiles">')
    parts.append(f'<div class="tile"><h3>{_esc(state.get("rung", DEFAULT_RUNG))} '
                 f'(:{_esc(state.get("port", "?"))})</h3>'
                 f'<div class="metric {cls}">{_esc(verdict)}</div></div>')
    obs_txt = f"{obs:.1f}" if isinstance(obs, (int, float)) else "-"
    base_txt = f"{base:.1f}" if isinstance(base, (int, float)) else "-"
    parts.append(f'<div class="tile"><h3>Observed / baseline tok/s</h3>'
                 f'<div class="metric">{obs_txt} <span class="muted" style="font-size:0.5em">/ {base_txt}</span></div></div>')
    frac_txt = f"{frac:.0%}" if isinstance(frac, (int, float)) else "-"
    parts.append(f'<div class="tile"><h3>Fraction of epoch</h3><div class="metric {cls}">{frac_txt}</div></div>')
    age = state.get("observed_age_s")
    age_txt = f"{int(age)} s" if isinstance(age, (int, float)) else "-"
    parts.append(f'<div class="tile"><h3>Deep sample age</h3><div class="metric">{age_txt}</div></div>')
    parts.append("</div>")

    parts.append('<dl class="kv">')
    parts.append(f"<dt>baseline epoch</dt><dd>{_esc(state.get('baseline_epoch') or '-')}</dd>")
    fb, wb = env.get("fail_below"), env.get("warn_below")
    env_txt = (f"warn &lt; {wb:.0%} · fail &lt; {fb:.0%}"
               if isinstance(fb, (int, float)) and isinstance(wb, (int, float)) else "-")
    parts.append(f"<dt>envelope</dt><dd>{env_txt}</dd>")
    lp = state.get("last_ping_ok")
    lp_txt = "-" if lp is None else ("ok" if lp else '<span class="bad">FAILED</span>')
    parts.append(f"<dt>last ping</dt><dd>{lp_txt}</dd>")
    stall = state.get("prefill_stall_recent")
    parts.append(f"<dt>prefill stall (recent)</dt><dd>{'<span class=\"bad\">yes</span>' if stall else 'no'}</dd>")
    parts.append(f"<dt>deep samples in tail</dt><dd>{_esc(state.get('deep_samples', 0))}</dd>")
    parts.append(f"<dt>observed at</dt><dd>{_esc(state.get('observed_at') or '-')}</dd>")
    excl = state.get("excluded_windows") or []
    parts.append(f"<dt>excluded windows</dt><dd>{_esc(', '.join(str(w) for w in excl)) or '-'}</dd>")
    if state.get("error"):
        parts.append(f'<dt>error</dt><dd class="bad">{_esc(state["error"])}</dd>')
    parts.append("</dl>")
    parts.append(f'<div class="muted" style="margin-top:8px;font-size:0.85em">{_esc(state.get("note") or "")}</div>')
    parts.append("</div>")
    return "\n".join(parts)


def _render_node_row(r: dict, all_services: bool) -> str:
    label, cls = _STATUS.get(r.get("status", "down"), ("?", "muted"))
    probes = r.get("probes") or []
    p = probes[0] if probes else None
    if p:
        tgt = f"{p.get('host')}:{p.get('port')}"
        svc = p.get("service", "-")
        lat = _fmt_ms(p.get("latency_ms")) if p.get("reachable") else _esc(p.get("error") or "-")
    else:
        tgt, svc, lat = "(no check)", "-", "-"
    out = [
        "<tr>",
        f'<td><span class="pill {cls}">{_esc(label)}</span></td>',
        f"<td><strong>{_esc(r.get('name'))}</strong></td>",
        f"<td>{_esc(r.get('kind'))}</td>",
        f"<td>{_esc(r.get('expect'))}</td>",
        f"<td><code>{_esc(tgt)}</code></td>",
        f"<td>{_esc(svc)}</td>",
        f"<td>{lat}</td>",
        f"<td>{_esc(r.get('purpose'))}</td>",
        "</tr>",
    ]
    extras = []
    if all_services and len(probes) > 1:
        for e in probes[1:]:
            ok = bool(e.get("reachable"))
            mark = '<span class="good">ok</span>' if ok else '<span class="bad">FAIL</span>'
            detail = _fmt_ms(e.get("latency_ms")) if ok else _esc(e.get("error") or "-")
            extras.append(f"{mark} <code>{_esc(e.get('host'))}:{_esc(e.get('port'))}</code> "
                          f"{_esc(e.get('service'))} {detail}")
    if r.get("status") == "down" and r.get("note"):
        extras.append(f'<span class="bad">note:</span> {_esc(r["note"])}')
    if extras:
        out.append('<tr class="sub"><td></td><td colspan="7">' + "<br>".join(extras) + "</td></tr>")
    return "\n".join(out)


def render_fleet_html(sweep_doc, generated_at, extras=None) -> str:
    """Render the sweep (fleet_ping --json shape, or a bare row list) as one HTML page.

    extras (all optional):
      rung_state     — P7's dict from hearth.health.rungstate (or None = unavailable);
                       the block is rendered only when the key is present.
      inventory_meta — the inventory's [meta] table (tailnet, updated, ...).
      all_services   — bool; render the per-service sub-rows.
      timeout        — the probe timeout that produced the sweep, for the header.
      notes          — list of operator strings rendered as bullets.
    """
    extras = extras or {}
    summary, rows = _normalize_sweep(sweep_doc)
    all_services = bool(extras.get("all_services"))
    meta = extras.get("inventory_meta") or {}
    when = _fmt_generated(generated_at)

    failures = summary.get("down", 0)
    if failures:
        headline = f'<span class="bad">{failures} expected-up node{"s" if failures != 1 else ""} DOWN</span>'
    elif not rows:
        headline = '<span class="muted">no nodes in the sweep</span>'
    else:
        headline = '<span class="good">every expected-up node reachable</span>'

    hdr_bits = [f"Generated at: {_esc(when)}"]
    if meta.get("tailnet"):
        hdr_bits.append(f"Inventory: {_esc(meta.get('tailnet'))}")
    if meta.get("updated"):
        hdr_bits.append(f"inventory updated {_esc(meta.get('updated'))}")
    hdr_bits.append("Probe: " + ("all declared services" if all_services else "primary service only"))
    if extras.get("timeout") is not None:
        hdr_bits.append(f"timeout {_esc(extras['timeout'])} s")

    parts = [
        "<!DOCTYPE html>", "<html>", "<head>", '<meta charset="utf-8">',
        '<meta http-equiv="refresh" content="300">',
        f"<title>{_esc(PAGE_TITLE)}</title>",
        "<style>", _CSS.strip(), "</style>", "</head>", "<body>",
        f"<h1>{_esc(PAGE_TITLE)}</h1>",
        '<div class="muted" style="margin-bottom: 24px; font-size: 0.9em;">' + " &bull; ".join(hdr_bits) + "</div>",
        '<div class="card" style="border-color: var(--warn);"><strong>Generated snapshot, not a live query.</strong> '
        "The browser refresh reloads this file; it does not re-probe the fleet. Regenerate with "
        "<code>python -m fleet.fleet_dashboard --all-services</code> on OMEN. "
        "A TCP connect proves a <em>listener</em>, not a serving model — port-open &ne; model-ready.</div>",
        '<div class="card">',
        '<div class="tiles">',
        f'<div class="tile"><h3>Fleet</h3><div class="metric" style="font-size:1.3em">{headline}</div></div>',
        f'<div class="tile"><h3>Up</h3><div class="metric good">{_esc(summary.get("up", 0))}</div></div>',
        f'<div class="tile"><h3>Down (expected up)</h3><div class="metric {"bad" if failures else ""}">{_esc(failures)}</div></div>',
        f'<div class="tile"><h3>Offline (optional)</h3><div class="metric muted">{_esc(summary.get("offline", 0))}</div></div>',
        f'<div class="tile"><h3>Nodes</h3><div class="metric">{_esc(summary.get("total", len(rows)))}</div></div>',
        "</div>", "</div>",
    ]

    if "rung_state" in extras:
        parts.append(_render_rung_state(extras.get("rung_state")))

    parts.append('<div class="card"><h2>Reachability</h2><div class="wrap"><table>')
    parts.append("<tr><th>Status</th><th>Node</th><th>Kind</th><th>Expect</th>"
                 "<th>Primary target</th><th>Service</th><th>Latency</th><th>Purpose</th></tr>")
    if rows:
        for r in rows:
            parts.append(_render_node_row(r, all_services))
    else:
        parts.append('<tr><td colspan="8" class="muted">no nodes in the sweep</td></tr>')
    parts.append("</table></div>")
    parts.append('<div class="muted" style="margin-top:10px;font-size:0.85em">'
                 "<strong>DOWN</strong> = an <code>expect=\"up\"</code> node whose primary target did not answer "
                 "(the sweep's alarm). <strong>offline</strong> = an <code>expect=\"optional\"</code> node "
                 "not answering — expected, never an alarm. A node is reachable when its <em>primary</em> "
                 "(first declared) service answers; extra services are shown as sub-rows with "
                 "<code>--all-services</code>.</div>")
    parts.append("</div>")

    notes = extras.get("notes") or []
    if notes:
        parts.append('<div class="card"><h2>Notes</h2><ul>')
        for n in notes:
            parts.append(f"<li>{_esc(n)}</li>")
        parts.append("</ul></div>")

    parts.append('<div class="muted" style="font-size:0.85em">Source: <code>fleet/inventory.toml</code> via '
                 "<code>fleet/fleet_ping.py</code>; rendered by <code>fleet/fleet_dashboard.py</code>. "
                 "OMEN-hosted; independent of the conductor.</div>")
    parts.extend(["</body>", "</html>", ""])
    return "\n".join(parts)


# --- collection (the impure part; injectable for tests) -------------------------

def build_sweep(inventory_path=DEFAULT_INVENTORY, all_services: bool = False,
                timeout: float = DEFAULT_TIMEOUT, prober=probe) -> dict:
    """Load the inventory and sweep it. Returns {summary, nodes, meta, all_services, timeout}.

    Raises FileNotFoundError / tomllib.TOMLDecodeError like fleet_ping does.
    """
    inv = load_inventory(Path(inventory_path))
    rows = sweep(inv["nodes"], all_services, timeout, prober=prober)
    return {
        "summary": summarize(rows),
        "nodes": rows,
        "meta": inv.get("meta", {}),
        "all_services": bool(all_services),
        "timeout": float(timeout),
    }


def collect_rung_state(rung: str = DEFAULT_RUNG, loader=None):
    """P7's rung-state dict, or None when hearth.health.rungstate is not importable.

    ``loader`` is injectable (a callable taking the rung name). Never raises —
    the dashboard renders with or without this block.
    """
    try:
        fn = loader
        if fn is None:
            from hearth.health.rungstate import live_rung_state as fn  # lazy, optional
        state = fn(rung)
    except Exception:  # noqa: BLE001 - best-effort block
        return None
    return state if isinstance(state, dict) else None


def main(argv=None, *, prober=probe, rung_state_fn=None) -> int:
    ap = argparse.ArgumentParser(description="Render the fleet reachability sweep as FLEET-DASHBOARD.html.")
    ap.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"HTML output (default {DEFAULT_OUT.name} at repo root)")
    ap.add_argument("--all-services", action="store_true", help="probe every declared service, not just primary")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--json-out", type=Path, default=None, help="also write the raw sweep feed as JSON")
    ap.add_argument("--no-rung-state", action="store_true", help="skip the omen-arc rate-health block")
    ap.add_argument("--rung", default=DEFAULT_RUNG, help="rung name for the rate-health block")
    a = ap.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        doc = build_sweep(a.inventory, a.all_services, a.timeout, prober=prober)
    except FileNotFoundError:
        print(f"inventory not found: {a.inventory}", file=sys.stderr)
        return 2
    except Exception as e:  # tomllib.TOMLDecodeError is a ValueError subclass
        if type(e).__name__ != "TOMLDecodeError":
            raise
        print(f"malformed inventory TOML: {e}", file=sys.stderr)
        return 2

    generated_at = datetime.now(timezone.utc)
    extras = {
        "inventory_meta": doc["meta"],
        "all_services": doc["all_services"],
        "timeout": doc["timeout"],
    }
    if not a.no_rung_state:
        extras["rung_state"] = collect_rung_state(a.rung, loader=rung_state_fn)

    page = render_fleet_html(doc, generated_at, extras)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(page, encoding="utf-8")

    if a.json_out:
        feed = {
            "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
            "summary": doc["summary"],
            "nodes": doc["nodes"],
            "meta": doc["meta"],
            "all_services": doc["all_services"],
            "timeout": doc["timeout"],
            "rung_state": extras.get("rung_state"),
        }
        a.json_out.parent.mkdir(parents=True, exist_ok=True)
        a.json_out.write_text(json.dumps(feed, indent=2), encoding="utf-8")

    s = doc["summary"]
    rs = extras.get("rung_state")
    rung_txt = (f", {a.rung}: {rs.get('verdict', 'unknown')}" if isinstance(rs, dict)
                else (", rung state: unavailable" if "rung_state" in extras else ""))
    print(f"wrote {a.out} ({s['up']} up | {s['down']} down | {s['offline']} offline, "
          f"{s['total']} nodes{rung_txt})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
