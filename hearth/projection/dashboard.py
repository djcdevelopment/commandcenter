"""HEARTH HTML dashboard projection (S5).

Aggregates knowledge store files and recent ledger events into a standalone
HTML dashboard (repo-root HEARTH-DASHBOARD.html, refreshed by the
knowledge_rebuild timer path). Pure read, degrades gracefully on missing
files or a broken pool — zero frontier tokens involved in producing it.
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import sys
import urllib.request
from pathlib import Path

from hearth.toolsurface._scope import resolve_in_scope

DEFAULT_JAEGER_API = "http://192.168.12.233:16686/jaeger/api/traces?service=hearth&limit=10"
DEFAULT_MEDIA_DIR = Path(r"E:\omen\imagegen\data\outputs")


def collect_runtime_snapshot() -> dict:
    """Collect bounded operational state; every source fails independently."""
    snapshot: dict = {"traces": [], "trace_error": None, "tenancy": {}, "media": []}
    try:
        url = os.environ.get("HEARTH_JAEGER_API", DEFAULT_JAEGER_API)
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        snapshot["traces"] = (payload.get("data") or [])[:10]
    except Exception as exc:
        snapshot["trace_error"] = type(exc).__name__
    try:
        from hearth.execution.coordination import GpuTenancyStore
        from hearth.imagegen import handoff
        state = GpuTenancyStore().get("omen-b70-pool")
        agent = handoff.agent_status()
        snapshot["tenancy"] = {
            "state": state.state if state is not None else "llm",
            "owner": state.owner if state is not None else "arcserve",
            "session_id": state.session_id if state is not None else None,
            "queued": len(handoff.list_queued()), "running": len(handoff.list_claims()),
            "agent_available": agent.available,
            "lanes": ((agent.record or {}).get("lanes") or []),
        }
    except Exception as exc:
        snapshot["tenancy"] = {"error": type(exc).__name__}
    try:
        root = Path(os.environ.get("IMAGEGEN_OUTPUT_ROOT", str(DEFAULT_MEDIA_DIR))).resolve()
        rows = [path for path in root.iterdir()
                if path.is_file() and path.suffix.lower() in {".wav", ".mp4", ".webm"}]
        for path in sorted(rows, key=lambda item: item.stat().st_mtime, reverse=True)[:20]:
            stat = path.stat()
            snapshot["media"].append({
                "name": path.name, "uri": path.as_uri(), "size": stat.st_size,
                "modified": datetime.datetime.fromtimestamp(
                    stat.st_mtime, datetime.timezone.utc
                ).isoformat(), "kind": "audio" if path.suffix.lower() == ".wav" else "video",
            })
    except Exception:
        pass
    return snapshot


def _trace_svg(traces: list[dict]) -> str:
    rows = []
    for trace in traces:
        spans = trace.get("spans") if isinstance(trace, dict) else None
        if not isinstance(spans, list) or not spans:
            continue
        starts = [int(span.get("startTime", 0)) for span in spans]
        ends = [int(span.get("startTime", 0)) + int(span.get("duration", 0)) for span in spans]
        origin, finish = min(starts), max(ends)
        extent = max(1, finish - origin)
        parent = {}
        for span in spans:
            for ref in span.get("references") or []:
                if ref.get("refType") == "CHILD_OF":
                    parent[span.get("spanID")] = ref.get("spanID")
                    break
        def depth(span_id):
            seen, count = set(), 0
            while span_id in parent and span_id not in seen and count < 8:
                seen.add(span_id); span_id = parent[span_id]; count += 1
            return count
        for span in sorted(spans, key=lambda item: int(item.get("startTime", 0))):
            left = 330 + 520 * (int(span.get("startTime", 0)) - origin) / extent
            width = max(4, 520 * int(span.get("duration", 0)) / extent)
            label = ("  " * depth(span.get("spanID"))) + str(span.get("operationName") or "span")
            rows.append((label[:46], left, width,
                         int(span.get("duration", 0)) / 1000))
    if not rows:
        return '<div class="muted">No MediaGen traces available.</div>'
    height = 24 + len(rows) * 26
    parts = [
        '<div class="trace-frame">',
        f'<svg class="trace-svg" viewBox="0 0 1000 {height}" '
        'role="img" aria-label="Trace timeline">',
    ]
    for index, (label, left, width, duration_ms) in enumerate(rows):
        y = 19 + index * 26
        parts.append(f'<text class="trace-label" x="10" y="{y}">{_escape(label)}</text>')
        parts.append(
            f'<rect x="{left:.2f}" y="{y - 11}" width="{width:.2f}" '
            'height="12" rx="3"/>'
        )
        parts.append(
            f'<text class="trace-duration" x="870" y="{y}">{duration_ms:.0f} ms</text>'
        )
    parts.append('</svg></div>')
    return "".join(parts)


def _escape(val) -> str:
    if val is None or val == "":
        return "—"
    return html.escape(str(val))


def _fmt_num(n) -> str:
    if n is None or not isinstance(n, (int, float)):
        return "—"
    return f"{n:,}"


def _trial_budget() -> int | None:
    """The [trial] budget_tokens from the packaged pool, or None. Lazy import
    so a broken pool never breaks rendering."""
    try:
        from hearth.toolsurface.backends import load_pool
        raw = load_pool().trial.get("budget_tokens")
        return int(raw) if raw else None
    except Exception:
        return None


def build_dashboard_html(
    knowledge_dir: Path, ledger_path: Path, now_iso: str | None = None,
    runtime_snapshot: dict | None = None,
) -> str:
    """Build the HEARTH HTML dashboard string."""
    if now_iso:
        now = datetime.datetime.fromisoformat(now_iso)
    else:
        now = datetime.datetime.now(datetime.timezone.utc)

    cutoff = now - datetime.timedelta(hours=24)
    cutoff_iso = cutoff.isoformat()

    offload = {}
    offload_path = knowledge_dir / "offload.json"
    if offload_path.exists():
        try:
            offload = json.loads(offload_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    capacity = {}
    capacity_path = knowledge_dir / "capacity.json"
    if capacity_path.exists():
        try:
            capacity = json.loads(capacity_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    trial_limit = _trial_budget()

    events_24h = 0
    backend_calls: dict[str, int] = {}
    backend_ok: dict[str, int] = {}
    routing_counts = {"escalation": 0, "ask": 0, "payload": 0, "quality": 0}
    error_counts: dict[str, int] = {}

    if ledger_path.exists():
        try:
            with ledger_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue

                    if not isinstance(ev, dict):
                        continue

                    ts_str = ev.get("ts")
                    if not isinstance(ts_str, str) or ts_str < cutoff_iso:
                        continue

                    events_24h += 1
                    tool = ev.get("tool")
                    if tool == "local_generate":
                        bk = str(ev.get("backend") or "unknown")
                        backend_calls[bk] = backend_calls.get(bk, 0) + 1
                        if ev.get("ok"):
                            backend_ok[bk] = backend_ok.get(bk, 0) + 1

                    routed_by = ev.get("routed_by")
                    if routed_by and isinstance(routed_by, str):
                        if routed_by.startswith("escalation:"):
                            routing_counts["escalation"] += 1
                        elif routed_by.startswith("ask:"):
                            routing_counts["ask"] += 1
                        elif routed_by.startswith("payload:"):
                            routing_counts["payload"] += 1
                        elif routed_by.startswith("quality-"):
                            routing_counts["quality"] += 1

                    err = ev.get("error_code")
                    if err:
                        err_str = str(err)
                        error_counts[err_str] = error_counts.get(err_str, 0) + 1
        except Exception:
            pass

    per_class = offload.get("per_class") or {}
    trial_data = per_class.get("trial") or {}
    trial_burn = (trial_data.get("tokens_in") or 0) + (trial_data.get("tokens_out") or 0)

    pct = 0
    if trial_limit and trial_limit > 0:
        pct = min(100, max(0, int((trial_burn / trial_limit) * 100)))

    bar_html = f'<div class="bar-bg"><div class="bar-fg" style="width: {pct}%;"></div></div>'
    limit_str = _fmt_num(trial_limit) if trial_limit else "—"
    burn_str = f"{_fmt_num(trial_burn)} / {limit_str}"

    html_lines = []
    html_lines.append('<!DOCTYPE html>')
    html_lines.append('<html>')
    html_lines.append('<head>')
    html_lines.append('<meta charset="utf-8">')
    html_lines.append('<meta http-equiv="refresh" content="300">')
    html_lines.append('<title>HEARTH Dashboard</title>')
    html_lines.append('<style>')
    html_lines.append(':root {')
    html_lines.append('  --bg: #faf8f4; --fg: #22211e; --muted: #6b6760; --accent: #b4541e;')
    html_lines.append('  --card: #ffffff; --border: #e2ddd3; --good: #2e7d32; --warn: #b26a00; --bad: #c62828; --code-bg: #f0ece4;')
    html_lines.append('}')
    html_lines.append('@media (prefers-color-scheme: dark) {')
    html_lines.append('  :root {')
    html_lines.append('    --bg: #1c1b18; --fg: #e8e4dc; --muted: #a09a8e; --accent: #e8925a;')
    html_lines.append('    --card: #262420; --border: #3a372f; --good: #81c784; --warn: #ffb74d; --bad: #ef9a9a; --code-bg: #2e2b25;')
    html_lines.append('  }')
    html_lines.append('}')
    html_lines.append('body { background: var(--bg); color: var(--fg); font-family: system-ui, sans-serif; padding: 20px; line-height: 1.5; margin: 0; }')
    html_lines.append('.card { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 15px; margin-bottom: 20px; }')
    html_lines.append('table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.9em; }')
    html_lines.append('th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); }')
    html_lines.append('th { color: var(--muted); font-weight: normal; }')
    html_lines.append('.bar-bg { background: var(--border); border-radius: 4px; overflow: hidden; height: 20px; width: 100%; margin-top: 8px; }')
    html_lines.append('.bar-fg { background: var(--accent); height: 100%; }')
    html_lines.append('.metric { font-size: 2em; margin-top: 8px; font-weight: 500; }')
    html_lines.append('h1, h2, h3 { margin-top: 0; font-weight: 500; }')
    html_lines.append('h3 { color: var(--muted); font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.5px; }')
    html_lines.append('.muted { color: var(--muted); }')
    html_lines.append('.trace-frame { width: 100%; overflow-x: auto; }')
    html_lines.append('.trace-svg { display: block; width: 100%; min-width: 760px; height: auto; }')
    html_lines.append('.trace-svg rect { fill: var(--accent); opacity: .9; }')
    html_lines.append('.trace-svg text { fill: var(--fg); font-family: system-ui, sans-serif; font-size: 12px; }')
    html_lines.append('.trace-svg .trace-label { font-weight: 500; }')
    html_lines.append('.trace-svg .trace-duration { fill: var(--muted); font-size: 11px; }')
    html_lines.append('audio, video { width: 100%; max-width: 640px; margin-top: 6px; }')
    html_lines.append('</style>')
    html_lines.append('</head>')
    html_lines.append('<body>')

    now_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    cap_wm = _escape(capacity.get("evidence_watermark"))
    off_wm = _escape(offload.get("evidence_watermark"))

    html_lines.append('<h1>HEARTH Dashboard</h1>')
    html_lines.append('<div style="color: var(--muted); margin-bottom: 24px; font-size: 0.9em;">')
    html_lines.append(f'Generated at: {_escape(now_str)} &bull; Capacity watermark: {cap_wm} &bull; Offload watermark: {off_wm}')
    html_lines.append('</div>')

    runtime = runtime_snapshot or {"traces": [], "tenancy": {}, "media": []}
    tenancy = runtime.get("tenancy") or {}
    html_lines.append('<div class="card"><h2>GPU Tenancy</h2>')
    if tenancy.get("error"):
        html_lines.append(f'<div class="muted">Unavailable: {_escape(tenancy["error"])}</div>')
    else:
        html_lines.append('<table><tr><th>State</th><th>Owner</th><th>Queued</th><th>Running</th><th>Agent</th></tr>')
        html_lines.append('<tr>' + ''.join([
            f'<td>{_escape(tenancy.get("state"))}</td>', f'<td>{_escape(tenancy.get("owner"))}</td>',
            f'<td>{_fmt_num(tenancy.get("queued"))}</td>', f'<td>{_fmt_num(tenancy.get("running"))}</td>',
            f'<td>{"ready" if tenancy.get("agent_available") else "unavailable"}</td>',
        ]) + '</tr></table>')
        lanes = tenancy.get("lanes") or []
        if lanes:
            html_lines.append('<div class="muted">Lanes: ' + ', '.join(
                _escape(item.get("lane_id") if isinstance(item, dict) else item) for item in lanes
            ) + '</div>')
    html_lines.append('</div>')

    html_lines.append('<div class="card"><h2>Jaeger Trace Timeline</h2>')
    if runtime.get("trace_error"):
        html_lines.append(f'<div class="muted">Jaeger unavailable: {_escape(runtime["trace_error"])}</div>')
    html_lines.append(_trace_svg(runtime.get("traces") or []))
    html_lines.append('</div>')

    html_lines.append('<div class="card"><h2>Media Gallery</h2>')
    media = runtime.get("media") or []
    if not media:
        html_lines.append('<div class="muted">No WAV, MP4, or WebM outputs.</div>')
    for item in media:
        name, uri = _escape(item.get("name")), _escape(item.get("uri"))
        html_lines.append(f'<div style="margin-bottom:16px"><a href="{uri}">{name}</a> '
                          f'<span class="muted">({_fmt_num(item.get("size"))} bytes)</span>')
        tag = "audio" if item.get("kind") == "audio" else "video"
        html_lines.append(f'<{tag} controls preload="metadata" src="{uri}"></{tag}></div>')
    html_lines.append('</div>')
    html_lines.append(
        '<div class="card" style="border-color: var(--warn);">'
        '<strong>Generated snapshot, not a live query.</strong> '
        'The browser refresh reloads this file; it does not rebuild the capacity or offload projections. '
        'Use the displayed generation time and evidence watermarks when quoting a value.'
        '</div>'
    )

    html_lines.append('<div class="card" style="display: flex; gap: 20px; flex-wrap: wrap;">')

    html_lines.append('<div style="flex: 1; min-width: 200px;">')
    html_lines.append('<h3>Offload Ratio</h3>')
    html_lines.append(f'<div class="metric">{_escape(offload.get("offload_ratio"))}</div>')
    html_lines.append('</div>')

    html_lines.append('<div style="flex: 1; min-width: 200px;">')
    html_lines.append('<h3>Est. USD Saved</h3>')
    usd = (offload.get("est_usd_saved") or {}).get("usd")
    usd_str = f"${usd:.2f}" if isinstance(usd, (float, int)) else "—"
    html_lines.append(f'<div class="metric" style="color: var(--good);">{usd_str}</div>')
    html_lines.append('</div>')

    html_lines.append('<div style="flex: 1; min-width: 200px;">')
    html_lines.append('<h3>Real USD Spent (trial)</h3>')
    real_block = offload.get("real_usd_spent") or {}
    real_usd = real_block.get("usd")
    if isinstance(real_usd, (float, int)):
        unpriced = real_block.get("unpriced_calls")
        suffix = f' <span style="font-size: 0.5em; color: var(--muted);">({_fmt_num(unpriced)} unpriced)</span>' if unpriced else ""
        real_str = f"${real_usd:.2f}{suffix}"
    else:
        real_str = "—"
    html_lines.append(f'<div class="metric" style="color: var(--warn, var(--muted));">{real_str}</div>')
    html_lines.append('</div>')

    html_lines.append('<div style="flex: 1; min-width: 200px;">')
    html_lines.append('<h3>Trial Burn</h3>')
    html_lines.append(f'<div class="metric" style="font-size: 1.2em; margin-bottom: 4px;">{burn_str}</div>')
    html_lines.append(bar_html)
    html_lines.append('</div>')

    html_lines.append('<div style="flex: 1; min-width: 200px;">')
    html_lines.append('<h3>24h Events</h3>')
    html_lines.append(f'<div class="metric">{_fmt_num(events_24h)}</div>')
    html_lines.append('</div>')

    html_lines.append('</div>')

    html_lines.append('<div class="card" style="display: flex; gap: 20px; flex-wrap: wrap;">')
    for label, key in [("Escalations", "escalation"), ("Asks", "ask"), ("Payload Routes", "payload"), ("Quality Calls", "quality")]:
        html_lines.append('<div style="flex: 1; min-width: 150px;">')
        html_lines.append(f'<h3>{label} (24h)</h3>')
        html_lines.append(f'<div class="metric">{_fmt_num(routing_counts[key])}</div>')
        html_lines.append('</div>')
    html_lines.append('</div>')

    html_lines.append('<div class="card">')
    html_lines.append('<h2>All-Time Buckets (offload.json)</h2>')
    html_lines.append('<table>')
    html_lines.append('<tr><th>Backend</th><th>Cost Class</th><th>Calls</th><th>OK Rate</th><th>Tokens In</th><th>Tokens Out</th><th>Last Seen</th></tr>')
    buckets = offload.get("buckets", [])
    if isinstance(buckets, list) and buckets:
        for b in buckets:
            if not isinstance(b, dict):
                continue
            html_lines.append('<tr>')
            html_lines.append(f'<td>{_escape(b.get("backend"))}</td>')
            html_lines.append(f'<td>{_escape(b.get("cost_class"))}</td>')
            html_lines.append(f'<td>{_fmt_num(b.get("calls"))}</td>')
            html_lines.append(f'<td>{_escape(b.get("ok_rate"))}</td>')
            html_lines.append(f'<td>{_fmt_num(b.get("tokens_in"))}</td>')
            html_lines.append(f'<td>{_fmt_num(b.get("tokens_out"))}</td>')
            html_lines.append(f'<td>{_escape(b.get("last_seen"))}</td>')
            html_lines.append('</tr>')
    else:
        html_lines.append('<tr><td colspan="7">No data.</td></tr>')
    html_lines.append('</table>')
    html_lines.append('</div>')

    html_lines.append('<div style="display: flex; gap: 20px; flex-wrap: wrap;">')

    html_lines.append('<div class="card" style="flex: 1; min-width: 300px;">')
    html_lines.append('<h2>24h Local Generate</h2>')
    html_lines.append('<table>')
    html_lines.append('<tr><th>Backend</th><th>Calls</th><th>OK</th></tr>')
    if backend_calls:
        for bk in sorted(backend_calls.keys()):
            html_lines.append('<tr>')
            html_lines.append(f'<td>{_escape(bk)}</td>')
            html_lines.append(f'<td>{_fmt_num(backend_calls[bk])}</td>')
            html_lines.append(f'<td>{_fmt_num(backend_ok.get(bk, 0))}</td>')
            html_lines.append('</tr>')
    else:
        html_lines.append('<tr><td colspan="3">No events in last 24h.</td></tr>')
    html_lines.append('</table>')
    html_lines.append('</div>')

    html_lines.append('<div class="card" style="flex: 1; min-width: 300px;">')
    html_lines.append('<h2>24h Error Codes</h2>')
    html_lines.append('<table>')
    html_lines.append('<tr><th>Error Code</th><th>Count</th></tr>')
    if error_counts:
        for ec, cnt in sorted(error_counts.items(), key=lambda item: (-item[1], item[0])):
            html_lines.append('<tr>')
            html_lines.append(f'<td>{_escape(ec)}</td>')
            html_lines.append(f'<td>{_fmt_num(cnt)}</td>')
            html_lines.append('</tr>')
    else:
        html_lines.append('<tr><td colspan="2">No errors in last 24h.</td></tr>')
    html_lines.append('</table>')
    html_lines.append('</div>')

    html_lines.append('</div>')
    html_lines.append('</body>')
    html_lines.append('</html>')

    return "\n".join(html_lines)


def write_dashboard(out_path: Path, knowledge_dir: Path, ledger_path: Path) -> dict:
    """Write the dashboard HTML to out_path."""
    html_content = build_dashboard_html(
        knowledge_dir, ledger_path, runtime_snapshot=collect_runtime_snapshot()
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_bytes = html_content.encode("utf-8")
    out_path.write_bytes(out_bytes)
    return {"path": str(out_path), "bytes": len(out_bytes)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.projection.dashboard",
        description="Generate the HEARTH HTML dashboard.",
    )
    parser.add_argument("--knowledge", default="knowledge")
    parser.add_argument("--ledger", default="hearth/var/ledger/events.ndjson")
    parser.add_argument("--out", default="hearth/var/dashboard/HEARTH-DASHBOARD.html")

    if argv is None:
        argv = sys.argv[1:]
    args = parser.parse_args(argv)

    k_dir = resolve_in_scope(args.knowledge)
    l_path = resolve_in_scope(args.ledger)
    o_path = resolve_in_scope(args.out)

    try:
        result = write_dashboard(o_path, k_dir, l_path)
        print(f"dashboard: OK {result['path']} ({result['bytes']} bytes)")
        return 0
    except Exception as exc:
        print(f"dashboard: FAILED {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
