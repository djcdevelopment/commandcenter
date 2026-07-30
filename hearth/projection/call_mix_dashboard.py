"""Generate a privacy-safe view of the call mix observed at the HEARTH boundary.

The kernel ledger contains digests and short previews, but this projection emits
aggregates only: counts, dates, call families, and inference token totals. It
deliberately never renders caller ids, paths, args_preview, result previews, or
errors.

Three charts answer different questions:

1. What kinds of calls crossed HEARTH, by all-time volume?
2. How did the daily mix change over the life of the ledger?
3. Where did inference input/output tokens land?

Calls made around HEARTH are outside the kernel ledger. The optional Ollama
sentinel file is summarized separately as connection observations and is never
merged into call counts because it is sampled, often unattributable, and has no
request or token payload.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hearth.kernel.capabilities import TOOL_CAPABILITY
from hearth.toolsurface._scope import resolve_in_scope


DEFAULT_LEDGER = "hearth/var/ledger/events.ndjson"
DEFAULT_SENTINEL = "hearth/var/sentinel/ollama-direct.ndjson"
DEFAULT_OUT = "HEARTH-CALL-MIX.html"

CLOUD_BACKENDS = {"gcp-gemini", "gcp-gemini-pro"}

HEALTH_TOOLS = {
    "mechnet_watchdog.patrol_snapshot",
    "mechnet_watchdog.watchfire",
    "mechnet_watchdog.patrol_trend",
    "mechnet_watchdog.revive",
    "bankedfire_drain.tick",
    "patrol",
    "remediate",
}
LEARNING_TOOLS = {
    "mechnet_watchdog.hindsight",
    "mechnet_watchdog.dream",
    "record_event",
    "project",
    "rebuild_knowledge",
    "project_offload_knowledge",
    "query_beliefs_summary",
    "query_capabilities",
    "query_findings",
    "query_offload",
    "query_capacity",
}
FLEET_TOOLS = {
    "submit_task",
    "submit_batch",
    "task_status",
    "queue_status",
    "harvest_fleet_run",
    "list_fleet_runs",
    "create_build_request",
    "update_build_request",
    "close_build_request",
    "execute_build_request",
    "list_build_requests",
}
FILESYSTEM_TOOLS = {"read_file", "write_file", "list_dir", "glob_files"}
TEST_TOOLS = {"run_tests"}
SCHEDULER_TOOLS = {"propose_schedule", "schedule_hindsight"}
CATALOG_TOOLS = {"wake_am4", "query_am4_catalog", "gather_am4_catalog"}

FAMILY_ORDER = [
    "Health / automation",
    "Door status",
    "Learning / retro",
    "Local inference",
    "Cloud / remote inference",
    "Fleet / builds",
    "Git / VCS",
    "Filesystem",
    "Test / assay",
    "Catalog / hardware",
    "Scheduler",
    "Other",
]
FAMILY_COLORS = {
    "Health / automation": "#6b7f9e",
    "Door status": "#4e6f9e",
    "Learning / retro": "#9667d5",
    "Local inference": "#45b98b",
    "Cloud / remote inference": "#49a4d5",
    "Fleet / builds": "#da7b45",
    "Git / VCS": "#d5a249",
    "Filesystem": "#7eb1d1",
    "Test / assay": "#d56c83",
    "Catalog / hardware": "#a8875d",
    "Scheduler": "#8e7ad1",
    "Other": "#768094",
}
MACRO_ORDER = ["Operations", "Learning / retro", "Inference", "Work plane", "Other"]
MACRO_COLORS = {
    "Operations": "#5c759d",
    "Learning / retro": "#9667d5",
    "Inference": "#45b98b",
    "Work plane": "#da7b45",
    "Other": "#8b93a3",
}
BACKEND_COLORS = {
    "gcp-gemini-pro": "#49a4d5",
    "am4-moe": "#45b98b",
    "gcp-gemini": "#7eb1d1",
    "omen-ollama": "#d5a249",
    "historical / unattributed": "#768094",
}


def classify_event(event: dict[str, Any]) -> str:
    """Return a stable presentation family for one kernel-ledger event."""
    tool = str(event.get("tool") or "unknown")
    backend = str(event.get("backend") or "")
    if tool == "local_generate":
        if backend in CLOUD_BACKENDS or backend.startswith("gcp-"):
            return "Cloud / remote inference"
        return "Local inference"
    if tool.startswith("git_"):
        return "Git / VCS"
    if tool in FILESYSTEM_TOOLS:
        return "Filesystem"
    if tool in TEST_TOOLS:
        return "Test / assay"
    if tool in FLEET_TOOLS:
        return "Fleet / builds"
    if tool in LEARNING_TOOLS:
        return "Learning / retro"
    if tool in HEALTH_TOOLS:
        return "Health / automation"
    if tool == "kernel_status":
        return "Door status"
    if tool in SCHEDULER_TOOLS:
        return "Scheduler"
    if tool in CATALOG_TOOLS:
        return "Catalog / hardware"
    return "Other"


def _macro_for_family(family: str) -> str:
    if family in {"Health / automation", "Door status"}:
        return "Operations"
    if family == "Learning / retro":
        return "Learning / retro"
    if family in {"Local inference", "Cloud / remote inference"}:
        return "Inference"
    if family in {"Fleet / builds", "Git / VCS", "Filesystem", "Test / assay"}:
        return "Work plane"
    return "Other"


def _int_cost(value: Any) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return 0


def _load_sentinel_summary(path: Path | None) -> dict[str, Any]:
    summary = {
        "available": False,
        "observations": 0,
        "parse_errors": 0,
        "unattributed": 0,
        "first_day": None,
        "last_day": None,
    }
    if path is None or not path.exists():
        return summary

    summary["available"] = True
    days: list[str] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                summary["parse_errors"] += 1
                continue
            summary["observations"] += 1
            process = record.get("process")
            if not process or process == "System Idle Process":
                summary["unattributed"] += 1
            day = str(record.get("ts") or "")[:10]
            if len(day) == 10:
                days.append(day)
    if days:
        summary["first_day"] = min(days)
        summary["last_day"] = max(days)
    return summary


def summarize(
    ledger_path: Path,
    sentinel_path: Path | None = None,
    registered_tool_count: int | None = None,
) -> dict[str, Any]:
    """Aggregate one HEARTH ledger without carrying content-bearing fields."""
    families: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    daily: dict[str, Counter[str]] = defaultdict(Counter)
    backends: dict[str, Counter[str]] = defaultdict(Counter)
    days: list[str] = []
    total = 0
    parse_errors = 0
    ok = 0

    with ledger_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                parse_errors += 1
                continue
            if not isinstance(event, dict):
                parse_errors += 1
                continue

            total += 1
            ok += int(event.get("ok") is True)
            tool = str(event.get("tool") or "unknown")
            family = classify_event(event)
            families[family] += 1
            tools[tool] += 1

            day = str(event.get("ts") or "")[:10]
            if len(day) == 10:
                days.append(day)
                daily[day][_macro_for_family(family)] += 1

            if tool == "local_generate":
                backend = str(event.get("backend") or "historical / unattributed")
                bucket = backends[backend]
                bucket["calls"] += 1
                bucket["ok"] += int(event.get("ok") is True)
                cost = event.get("cost") if isinstance(event.get("cost"), dict) else {}
                tokens_in = cost.get("tokens_in")
                tokens_out = cost.get("tokens_out")
                if isinstance(tokens_in, (int, float)) or isinstance(tokens_out, (int, float)):
                    bucket["token_receipts"] += 1
                    bucket["tokens_in"] += _int_cost(tokens_in)
                    bucket["tokens_out"] += _int_cost(tokens_out)

    registered = len(TOOL_CAPABILITY) if registered_tool_count is None else registered_tool_count
    backend_rows = []
    for name, values in backends.items():
        backend_rows.append({
            "backend": name,
            "calls": values["calls"],
            "ok": values["ok"],
            "token_receipts": values["token_receipts"],
            "tokens_in": values["tokens_in"],
            "tokens_out": values["tokens_out"],
        })
    backend_rows.sort(
        key=lambda row: (row["tokens_in"] + row["tokens_out"], row["calls"]),
        reverse=True,
    )

    daily_rows = []
    for day in sorted(daily):
        daily_rows.append({
            "day": day,
            "series": {name: daily[day].get(name, 0) for name in MACRO_ORDER},
        })

    return {
        "events": total,
        "ok": ok,
        "parse_errors": parse_errors,
        "first_day": min(days) if days else None,
        "last_day": max(days) if days else None,
        "family_counts": {name: families.get(name, 0) for name in FAMILY_ORDER},
        "tool_counts": dict(tools.most_common()),
        "observed_tool_count": len(tools),
        "registered_tool_count": registered,
        "daily": daily_rows,
        "backends": backend_rows,
        "sentinel": _load_sentinel_summary(sentinel_path),
    }


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _fmt_pct(value: int, total: int) -> str:
    return f"{(100.0 * value / total):.1f}%" if total else "0.0%"


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _family_chart(summary: dict[str, Any]) -> str:
    counts = summary["family_counts"]
    rows = [(name, counts[name]) for name in FAMILY_ORDER if counts[name]]
    total = summary["events"]
    maximum = max((value for _, value in rows), default=1)
    width = 1120
    left = 220
    bar_width = 690
    row_h = 38
    top = 30
    height = top + row_h * len(rows) + 30
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Observed HEARTH calls by family on a logarithmic scale">',
        '<text class="axis-note" x="220" y="18">logarithmic bar scale · exact count and share printed at right</text>',
    ]
    denominator = math.log10(maximum + 1)
    for index, (name, value) in enumerate(rows):
        y = top + index * row_h
        scaled = (math.log10(value + 1) / denominator) * bar_width if denominator else 0
        color = FAMILY_COLORS[name]
        parts.extend([
            f'<text class="label" x="{left - 14}" y="{y + 19}" text-anchor="end">{_esc(name)}</text>',
            f'<rect class="track" x="{left}" y="{y + 5}" width="{bar_width}" height="22" rx="5"/>',
            f'<rect x="{left}" y="{y + 5}" width="{scaled:.1f}" height="22" rx="5" fill="{color}">'
            f'<title>{_esc(name)}: {_fmt_int(value)} calls ({_fmt_pct(value, total)})</title></rect>',
            f'<text class="value" x="{left + bar_width + 18}" y="{y + 21}">'
            f'{_fmt_int(value)} · {_fmt_pct(value, total)}</text>',
        ])
    parts.append("</svg>")
    return "".join(parts)


def _daily_chart(summary: dict[str, Any]) -> str:
    rows = summary["daily"]
    width = 1160
    height = 430
    left = 72
    top = 28
    plot_w = 1040
    plot_h = 300
    max_total = max(
        (sum(row["series"].values()) for row in rows),
        default=1,
    )
    rounded_max = max(100, int(math.ceil(max_total / 200.0) * 200))
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Daily stacked HEARTH call volume by macro family">',
    ]
    for tick in range(0, 5):
        value = rounded_max * tick / 4
        y = top + plot_h - (value / rounded_max) * plot_h
        parts.extend([
            f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}"/>',
            f'<text class="tick" x="{left - 10}" y="{y + 4:.1f}" text-anchor="end">{_fmt_int(int(value))}</text>',
        ])

    count = max(len(rows), 1)
    slot = plot_w / count
    bar_w = max(8.0, slot - 5.0)
    for index, row in enumerate(rows):
        x = left + index * slot + (slot - bar_w) / 2
        cursor = top + plot_h
        total = sum(row["series"].values())
        for name in MACRO_ORDER:
            value = row["series"][name]
            if not value:
                continue
            segment_h = (value / rounded_max) * plot_h
            cursor -= segment_h
            parts.append(
                f'<rect x="{x:.1f}" y="{cursor:.1f}" width="{bar_w:.1f}" height="{segment_h:.1f}" '
                f'fill="{MACRO_COLORS[name]}"><title>{_esc(row["day"])} · {_esc(name)}: '
                f'{_fmt_int(value)}</title></rect>'
            )
        if index % 3 == 0 or index == len(rows) - 1:
            parts.append(
                f'<text class="tick" x="{x + bar_w / 2:.1f}" y="{top + plot_h + 18}" '
                f'text-anchor="middle">{_esc(row["day"][5:])}</text>'
            )
        parts.append(
            f'<title>{_esc(row["day"])} total: {_fmt_int(total)}</title>'
        )

    legend_y = 375
    legend_x = left
    for name in MACRO_ORDER:
        parts.extend([
            f'<rect x="{legend_x}" y="{legend_y}" width="14" height="14" rx="3" fill="{MACRO_COLORS[name]}"/>',
            f'<text class="legend" x="{legend_x + 21}" y="{legend_y + 12}">{_esc(name)}</text>',
        ])
        legend_x += 195
    parts.append("</svg>")
    return "".join(parts)


def _token_chart(summary: dict[str, Any]) -> str:
    rows = summary["backends"]
    width = 1160
    left_label = 210
    in_x = 230
    out_x = 650
    bar_w = 310
    row_h = 54
    top = 58
    height = top + row_h * max(len(rows), 1) + 44
    max_in = max((row["tokens_in"] for row in rows), default=1) or 1
    max_out = max((row["tokens_out"] for row in rows), default=1) or 1
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Inference input and output tokens by backend">',
        f'<text class="axis-title" x="{in_x}" y="22">INPUT TOKENS · independent scale, max {_fmt_int(max_in)}</text>',
        f'<text class="axis-title" x="{out_x}" y="22">OUTPUT TOKENS · independent scale, max {_fmt_int(max_out)}</text>',
        '<text class="axis-title" x="1000" y="22">CALLS / RECEIPTS</text>',
    ]
    for index, row in enumerate(rows):
        y = top + index * row_h
        name = row["backend"]
        color = BACKEND_COLORS.get(name, "#768094")
        in_width = (row["tokens_in"] / max_in) * bar_w
        out_width = (row["tokens_out"] / max_out) * bar_w
        parts.extend([
            f'<text class="label" x="{left_label}" y="{y + 17}" text-anchor="end">{_esc(name)}</text>',
            f'<rect class="track" x="{in_x}" y="{y}" width="{bar_w}" height="24" rx="5"/>',
            f'<rect x="{in_x}" y="{y}" width="{in_width:.1f}" height="24" rx="5" fill="{color}"/>',
            f'<text class="bar-value" x="{in_x + 8}" y="{y + 17}">{_fmt_int(row["tokens_in"])}</text>',
            f'<rect class="track" x="{out_x}" y="{y}" width="{bar_w}" height="24" rx="5"/>',
            f'<rect x="{out_x}" y="{y}" width="{out_width:.1f}" height="24" rx="5" fill="{color}"/>',
            f'<text class="bar-value" x="{out_x + 8}" y="{y + 17}">{_fmt_int(row["tokens_out"])}</text>',
            f'<text class="value" x="1000" y="{y + 17}">{_fmt_int(row["calls"])} / '
            f'{_fmt_int(row["token_receipts"])}</text>',
        ])
    parts.append("</svg>")
    return "".join(parts)


def build_html(summary: dict[str, Any], generated_at: datetime | None = None) -> str:
    """Render one dependency-free HTML artifact from an aggregate summary."""
    now = generated_at or datetime.now(timezone.utc)
    generated = now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    families = summary["family_counts"]
    local_calls = families["Local inference"]
    cloud_calls = families["Cloud / remote inference"]
    inference_calls = local_calls + cloud_calls
    token_receipts = sum(row["token_receipts"] for row in summary["backends"])
    tokens_in = sum(row["tokens_in"] for row in summary["backends"])
    tokens_out = sum(row["tokens_out"] for row in summary["backends"])
    operations = families["Health / automation"] + families["Door status"]
    sentinel = summary["sentinel"]
    top_tools = list(summary["tool_counts"].items())[:12]

    sentinel_text = (
        f'The separate Ollama sentinel contains <strong>{_fmt_int(sentinel["observations"])}</strong> '
        f'sampled socket observations ({_fmt_int(sentinel["unattributed"])} unattributable) from '
        f'{_esc(sentinel["first_day"])} through {_esc(sentinel["last_day"])}. '
        if sentinel["available"] else
        "No Ollama sentinel file was available at generation time. "
    )

    top_tool_rows = "".join(
        f"<tr><td><code>{_esc(tool)}</code></td><td>{_fmt_int(count)}</td>"
        f"<td>{_fmt_pct(count, summary['events'])}</td></tr>"
        for tool, count in top_tools
    )
    backend_rows = "".join(
        "<tr>"
        f"<td><code>{_esc(row['backend'])}</code></td>"
        f"<td>{_fmt_int(row['calls'])}</td>"
        f"<td>{_fmt_int(row['token_receipts'])}</td>"
        f"<td>{_fmt_int(row['tokens_in'])}</td>"
        f"<td>{_fmt_int(row['tokens_out'])}</td>"
        f"<td>{_fmt_pct(row['ok'], row['calls'])}</td>"
        "</tr>"
        for row in summary["backends"]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HEARTH Observed Call Mix</title>
<style>
:root {{
  color-scheme: dark;
  --bg:#111622; --panel:#1c2433; --panel2:#222c3e; --line:#344158;
  --ink:#eef3ff; --muted:#aab7cf; --accent:#ff8053; --good:#63d5a6;
  --warn:#efb84b; --bad:#ee7384;
}}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.55 system-ui,sans-serif }}
main {{ width:min(1240px,calc(100% - 36px)); margin:auto; padding:28px 0 72px }}
header {{ padding:34px; border:1px solid var(--line); border-radius:18px;
  background:linear-gradient(120deg,#1c2433,#33231f) }}
.eyebrow {{ color:var(--accent); font-size:.76rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase }}
h1 {{ font-size:clamp(2.3rem,6vw,4.7rem); line-height:.98; margin:.28em 0 }}
h2 {{ margin:2.4rem 0 .3rem; font-size:1.7rem }}
h3 {{ margin:.1rem 0 .4rem; font-size:.85rem; color:#bdd0f2; text-transform:uppercase; letter-spacing:.08em }}
p {{ max-width:84ch }} .sub {{ color:var(--muted) }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; margin:20px 0 }}
.card,.callout,.chart-card {{ border:1px solid var(--line); background:var(--panel); border-radius:12px; padding:18px }}
.metric {{ font-size:2rem; font-weight:800 }}
.callout {{ border-left:5px solid var(--warn) }} .callout strong {{ color:#ffd37a }}
.chart-card {{ padding:22px; overflow-x:auto }}
.chart-card > p {{ color:var(--muted); margin-top:0 }}
.chart {{ display:block; width:100%; min-width:800px; height:auto }}
.chart text {{ font-family:system-ui,sans-serif; fill:var(--ink) }}
.chart .label {{ font-size:13px; font-weight:650 }}
.chart .value {{ font-size:12px; fill:var(--muted) }}
.chart .bar-value {{ font-size:11px; fill:#fff; font-weight:700 }}
.chart .axis-note,.chart .tick,.chart .legend {{ font-size:11px; fill:var(--muted) }}
.chart .axis-title {{ font-size:11px; fill:#bdd0f2; font-weight:750; letter-spacing:.05em }}
.chart .track {{ fill:#2a3548 }} .chart .grid {{ stroke:#344158; stroke-width:1 }}
table {{ width:100%; border-collapse:collapse; margin-top:14px }}
th,td {{ padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top }}
th {{ color:#bdd0f2; font-size:.76rem; text-transform:uppercase; letter-spacing:.06em }}
td:nth-child(n+2) {{ font-variant-numeric:tabular-nums }}
code {{ background:#2a3548; padding:.12em .35em; border-radius:4px }}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:16px }}
.badge {{ display:inline-block; border:1px solid currentColor; border-radius:999px; padding:.15em .58em;
  font-size:.68rem; font-weight:800; letter-spacing:.06em; color:var(--warn) }}
footer {{ margin-top:36px; color:var(--muted); border-top:1px solid var(--line); padding-top:16px }}
@media(max-width:800px) {{ .two {{ grid-template-columns:1fr }} header {{ padding:24px }} }}
</style>
</head>
<body>
<main>
<header>
  <div class="eyebrow">Generated aggregate · {_esc(generated)} · kernel ledger watermark {_esc(summary["last_day"])}</div>
  <h1>HEARTH<br>Observed Call Mix</h1>
  <p>A privacy-safe picture of what crossed the authenticated HEARTH boundary:
  volume, time, call family, and inference token flow. No prompt, path, caller,
  preview, result, or error content is rendered.</p>
</header>

<div class="grid">
  <div class="card"><h3>Boundary events</h3><div class="metric">{_fmt_int(summary["events"])}</div><p class="sub">{_esc(summary["first_day"])} → {_esc(summary["last_day"])} · {summary["parse_errors"]} parse errors</p></div>
  <div class="card"><h3>Observed surface</h3><div class="metric">{summary["observed_tool_count"]} / {summary["registered_tool_count"]}</div><p class="sub">tools observed / currently registered; absence is not a failed tool</p></div>
  <div class="card"><h3>Inference calls</h3><div class="metric">{_fmt_int(inference_calls)}</div><p class="sub">{_fmt_int(local_calls)} local · {_fmt_int(cloud_calls)} cloud/remote</p></div>
  <div class="card"><h3>Token receipts</h3><div class="metric">{_fmt_int(token_receipts)}</div><p class="sub">{_fmt_int(tokens_in)} in · {_fmt_int(tokens_out)} out</p></div>
  <div class="card"><h3>Automation share</h3><div class="metric">{_fmt_pct(operations, summary["events"])}</div><p class="sub">health loops + door status; the dominant ledger workload</p></div>
</div>

<div class="callout">
  <span class="badge">COVERAGE BOUNDARY</span>
  <p><strong>This is total observed HEARTH traffic, not total activity on the machines.</strong>
  Calls made directly to Ollama, SSH, Git, files, or cloud APIs never become kernel-ledger
  events. {sentinel_text}Those observations are not request counts, can miss short calls,
  and contain no prompt/token data, so this page does not merge them with HEARTH calls.</p>
</div>

<section>
  <h2>1 · Calls by semantic family</h2>
  <div class="chart-card">
    <p>The exact labels carry the truth; logarithmic bars keep the long tail visible beside unattended loops.</p>
    {_family_chart(summary)}
  </div>
</section>

<section>
  <h2>2 · Daily traffic mix</h2>
  <div class="chart-card">
    <p>Linear stacked volume. The July 21 step-change is mostly the always-on operations cadence, not a sudden burst of human prompts.</p>
    {_daily_chart(summary)}
  </div>
</section>

<section>
  <h2>3 · Inference token flow by backend</h2>
  <div class="chart-card">
    <p>Input and output use separate scales so both distributions remain legible.
    “Historical / unattributed” means old <code>local_generate</code> events that predate backend stamping.</p>
    {_token_chart(summary)}
    <table>
      <thead><tr><th>Backend</th><th>Calls</th><th>Token receipts</th><th>Tokens in</th><th>Tokens out</th><th>OK rate</th></tr></thead>
      <tbody>{backend_rows}</tbody>
    </table>
  </div>
</section>

<section class="two">
  <div>
    <h2>What dominates?</h2>
    <div class="chart-card">
      <table>
        <thead><tr><th>Tool</th><th>Calls</th><th>Share</th></tr></thead>
        <tbody>{top_tool_rows}</tbody>
      </table>
    </div>
  </div>
  <div>
    <h2>How types were assigned</h2>
    <div class="chart-card">
      <table>
        <thead><tr><th>Family</th><th>Rule</th></tr></thead>
        <tbody>
          <tr><td>Local inference</td><td><code>local_generate</code> on OMEN/AM4, plus historical calls without backend stamps</td></tr>
          <tr><td>Cloud / remote</td><td><code>local_generate</code> on a <code>gcp-*</code> backend</td></tr>
          <tr><td>Health / automation</td><td>patrol, Watchfire, trend, revive, and idle-drain ticks</td></tr>
          <tr><td>Learning / retro</td><td>hindsight, dream, workflow records, knowledge queries/projectors</td></tr>
          <tr><td>Fleet / builds</td><td>submit/status/queue, harvest, and build-request lifecycle</td></tr>
          <tr><td>Developer work</td><td>filesystem, Git, and test/assay tools stay separate</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</section>

<footer>
  Source: <code>{_esc(DEFAULT_LEDGER)}</code> plus a non-additive coverage summary from
  <code>{_esc(DEFAULT_SENTINEL)}</code>. Generated by
  <code>python -m hearth.projection.call_mix_dashboard</code> and refreshed by the
  six-hour knowledge-rebuild timer. Refreshing the browser only reloads the latest
  static artifact.
</footer>
</main>
</body>
</html>
"""


def write_dashboard(
    out_path: Path,
    ledger_path: Path,
    sentinel_path: Path | None = None,
) -> dict[str, Any]:
    summary = summarize(ledger_path, sentinel_path=sentinel_path)
    rendered = build_html(summary)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    return {
        "path": str(out_path),
        "bytes": len(rendered.encode("utf-8")),
        "events": summary["events"],
        "observed_tools": summary["observed_tool_count"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.projection.call_mix_dashboard",
        description="Generate the privacy-safe HEARTH observed-call-mix page.",
    )
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--sentinel", default=DEFAULT_SENTINEL)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        ledger = resolve_in_scope(args.ledger)
        sentinel = resolve_in_scope(args.sentinel) if args.sentinel else None
        out = resolve_in_scope(args.out)
        result = write_dashboard(out, ledger, sentinel)
    except Exception as exc:
        print(f"call-mix dashboard: FAILED {exc}")
        return 1

    print(
        f"call-mix dashboard: OK {result['path']} ({result['bytes']} bytes, "
        f"{result['events']} events, {result['observed_tools']} observed tools)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
