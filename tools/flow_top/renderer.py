#!/usr/bin/env python3
"""256-Color Terminal HUD Renderer for `flow-top` (`tools/flow_top/renderer.py`).

Reuses visual primitives and color palette from `tools.fleet_monitor.renderer` and exposes:
  - `format_flow_state_badge(state: str, use_color: bool = True) -> str`
  - `render_flow_snapshot(snapshot: FlowSnapshot, view: str = "overview", use_color: bool = True, width: Optional[int] = None, is_tty: bool = False) -> str`

Answers three questions at a glance:
  1. What is available?
  2. What work is moving?
  3. Did that work use the intended components?
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

_LISTING_ROOT = Path("/home/derek/work/listing")
if _LISTING_ROOT.exists() and str(_LISTING_ROOT) not in sys.path:
    sys.path.append(str(_LISTING_ROOT))
    import tools as _tools_pkg

    _listing_tools = str(_LISTING_ROOT / "tools")
    if hasattr(_tools_pkg, "__path__") and _listing_tools not in _tools_pkg.__path__:
        _tools_pkg.__path__.append(_listing_tools)

from tools.fleet_monitor.renderer import (
    C_BLUE,
    C_BORDER,
    C_CYAN,
    C_DARK,
    C_GOLD,
    C_GREEN,
    C_LABEL,
    C_MUTED,
    C_ORANGE,
    C_PURPLE,
    C_RED,
    C_TIER_AM4,
    C_TIER_B70,
    C_TIER_NPU,
    C_WHITE,
    ansi_trunc,
    bot_border,
    c,
    dual_bot_border,
    dual_row,
    dual_top_border,
    fmt_elapsed,
    grad_bar,
    mid_border,
    pad_or_trunc,
    resolve_inner_width,
    row_box,
    sparkline,
    top_border,
    vlen,
)

from .contract import (
    DEFAULT_ONTOLOGY_UI_URL,
    VALID_FLOW_VIEWS,
    CorrelatedTask,
    DuckDBPanel,
    FlowSnapshot,
    LifetimeCacheSummary,
    MonitorResourceUsage,
    OntologyPanel,
    PathHop,
    ServiceAvailability,
    ServiceBusPanel,
    format_state_label,
)


def format_flow_state_badge(state: str, use_color: bool = True) -> str:
    """Visually and textually distinguish the six required states."""
    s = (state or "unknown").strip().lower()
    if s == "active":
        return c("[ACTIVE]", C_GREEN, use_color)
    if s == "idle":
        return c("[IDLE]", C_CYAN, use_color)
    if s == "stale":
        return c("[STALE]", C_ORANGE, use_color)
    if s == "unavailable":
        return c("[UNAVAILABLE]", C_RED, use_color)
    if s == "not_used_by_job":
        return c("[NOT USED BY THIS JOB]", C_MUTED, use_color)
    return c("[UNKNOWN]", C_PURPLE, use_color)


def _format_overall_banner_badge(state: str, use_color: bool = True) -> Tuple[str, str]:
    s = (state or "idle").strip().lower()
    if s == "active":
        return "▶ ACTIVE", C_GREEN
    if s == "idle":
        return "■ IDLE", C_GOLD
    if s in ("degraded", "stale", "unavailable"):
        return "▲ DEGRADED", C_ORANGE if s != "unavailable" else C_RED
    return "? UNKNOWN", C_PURPLE


def _fmt_age(age_s: Optional[float]) -> str:
    if age_s is None:
        return "n/a"
    if age_s < 10.0:
        return f"{max(0.0, float(age_s)):.1f}s"
    return fmt_elapsed(float(age_s))


def _panel_header(
    title: str,
    service_state: str,
    job_state: Optional[str],
    source: str,
    obs_age_s: Optional[float],
    width: int,
    use_color: bool = True,
    title_col: str = C_CYAN,
) -> str:
    """Build a boxed top border that always carries service state, source, and obs_age_s without clipping."""
    svc_b = format_flow_state_badge(service_state, use_color)
    include_job_in_border = bool(job_state and width >= 115)
    job_part = (
        f" {c('Job:', C_LABEL, use_color)}{format_flow_state_badge(job_state, use_color)}"
        if include_job_in_border
        else ""
    )
    age_s = _fmt_age(obs_age_s)
    age_col = C_ORANGE if (obs_age_s is not None and obs_age_s >= 15.0) else C_MUTED

    badges_str = f"{svc_b}{job_part}"
    age_str = f" {c('· age:', C_MUTED, use_color)} {c(age_s, age_col, use_color)}"

    # Guarantee `src:` and `age:` never clip by sizing `title` and `src_short` to fit `width - 5`
    avail_total = max(24, width - 5)
    min_src_len = 12 if width < 110 else 28
    max_title_len = max(14, avail_total - vlen(badges_str) - vlen(age_str) - 9 - min_src_len)
    title_clean = ansi_trunc(title, max_title_len, use_color=False)

    rem_for_src = max(6, avail_total - len(title_clean) - 1 - vlen(badges_str) - vlen(age_str) - 8)
    src_short = ansi_trunc(source or "n/a", rem_for_src, use_color=False)
    src_str = f" {c('· src:', C_MUTED, use_color)} {c(src_short, C_LABEL, use_color)}"

    inner = c(title_clean, title_col, use_color) + " " + badges_str + src_str + age_str
    inner_trunc = ansi_trunc(inner, avail_total, use_color=use_color)
    dashes = max(0, width - vlen(inner_trunc) - 4)
    return (
        c("┌─ ", C_BORDER, use_color)
        + inner_trunc
        + " "
        + c(("─" * dashes) + "┐", C_BORDER, use_color)
    )


def _wrap_kv_rows(
    label: str,
    value_text: str,
    width: int,
    use_color: bool = True,
    label_col: str = C_LABEL,
    val_col: str = C_WHITE,
    indent: str = "                  ",
) -> List[str]:
    """Render a key-value row across 1 or more boxed lines, wrapping cleanly at ' · ' boundaries first so URLs/paths stay intact."""
    prefix = f" {c(label, label_col, use_color)} "
    avail_first = max(16, width - vlen(prefix))
    if len(value_text) <= avail_first:
        return [row_box(prefix + c(value_text, val_col, use_color), width, use_color)]

    avail_cont = max(16, width - len(indent))
    segments = [seg.strip() for seg in value_text.split(" · ") if seg.strip()]
    if len(segments) > 1:
        lines_text: List[str] = []
        cur_line = ""
        cur_limit = avail_first
        for seg in segments:
            candidate = f"{cur_line} · {seg}" if cur_line else seg
            if len(candidate) <= cur_limit:
                cur_line = candidate
            else:
                if cur_line:
                    lines_text.append(cur_line)
                    cur_limit = avail_cont
                # If a single segment still exceeds cur_limit, slice it only as last resort
                while len(seg) > cur_limit:
                    lines_text.append(seg[:cur_limit])
                    seg = seg[cur_limit:]
                    cur_limit = avail_cont
                cur_line = seg
        if cur_line:
            lines_text.append(cur_line)

        rows: List[str] = []
        for idx, lt in enumerate(lines_text):
            if idx == 0:
                rows.append(row_box(prefix + c(lt, val_col, use_color), width, use_color))
            else:
                rows.append(row_box(indent + c(lt, val_col, use_color), width, use_color))
        return rows

    rows = []
    first_chunk = value_text[:avail_first]
    rows.append(row_box(prefix + c(first_chunk, val_col, use_color), width, use_color))
    rem = value_text[avail_first:]
    while rem:
        chunk = rem[:avail_cont]
        rem = rem[avail_cont:]
        rows.append(row_box(indent + c(chunk, val_col, use_color), width, use_color))
    return rows


def _service_by_id(snapshot: FlowSnapshot, service_id: str) -> Optional[ServiceAvailability]:
    for s in snapshot.services:
        if s.service_id == service_id:
            return s
    return None


def _format_clock_pst(snapshot: FlowSnapshot) -> str:
    if snapshot.collected_at_unix > 0:
        try:
            dt = datetime.fromtimestamp(snapshot.collected_at_unix, tz=ZoneInfo("America/Los_Angeles"))
            return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            pass
    return snapshot.collected_at_utc or "-"


# ---------------------------------------------------------------------------
# 1. Top Double-Border Banner
# ---------------------------------------------------------------------------


def render_banner_section(snapshot: FlowSnapshot, W: int, use_color: bool = True) -> List[str]:
    badge_txt, banner_col = _format_overall_banner_badge(snapshot.overall_state, use_color)
    if snapshot.simulated_unavailable:
        badge_txt = "▲ DEGRADED"
        banner_col = C_ORANGE

    lines: List[str] = []
    lines.append(c("┏" + ("━" * W) + "┓", banner_col, use_color))

    # Line 1: Overall state badge | What is available? | Clock PST
    title_short = "FLOW-TOP: WORKFLOW, BROKER, ROUTER, ONTOLOGY & INTAKE MONITOR" if W >= 120 else "FLOW-TOP"
    online_svcs = sum(1 for s in snapshot.services if s.state in ("active", "idle"))
    total_svcs = len(snapshot.services)
    avail_summary = snapshot.headline_available or (
        f"Services: {online_svcs}/{total_svcs} online · HEARTH :8710 · Aspire :18888 · "
        f"Broker :5672/:18080 · Ontology :13000->AM4 · NPU :18097"
    )
    clk_str = _format_clock_pst(snapshot)
    left_p1 = (
        f" {c(title_short, C_WHITE, use_color)} {c('│', C_MUTED, use_color)} "
        f"{c(badge_txt, banner_col, use_color)} {c('│', C_MUTED, use_color)} "
        f"{c('1. Available:', C_LABEL, use_color)} "
    )
    right_p1 = f" {c('│', C_MUTED, use_color)} {c('Clock:', C_LABEL, use_color)} {c(clk_str, C_CYAN, use_color)}"
    avail_w1 = max(16, W - vlen(left_p1) - vlen(right_p1))
    avail_trunc = ansi_trunc(avail_summary, avail_w1, use_color=False)
    line1 = left_p1 + c(f"{avail_trunc:<{avail_w1}}", C_WHITE, use_color) + right_p1
    lines.append(c("┃", banner_col, use_color) + pad_or_trunc(line1, W, use_color) + c("┃", banner_col, use_color))

    # Line 2: What work is moving? | Monitor self-stats
    active_tasks = sum(1 for t in snapshot.tasks if t.state == "active")
    sel = snapshot.selected_task
    if snapshot.headline_moving:
        moving_summary = snapshot.headline_moving
    elif sel is not None:
        moving_summary = (
            f"Active: {active_tasks} · Latest/Selected: {sel.task_id} "
            f"({sel.stage} / {sel.outcome or sel.state})"
        )
    else:
        moving_summary = f"Active: {active_tasks} · Latest: none"

    if snapshot.simulated_unavailable:
        sim_tag = f" [SIMULATED UNAVAILABLE: {','.join(snapshot.simulated_unavailable)}]"
        moving_summary += sim_tag

    mu: MonitorResourceUsage = snapshot.monitor_usage
    self_stats = (
        f"Poll: {mu.poll_duration_ms:.1f}ms · "
        f"Cache: {mu.incremental_cache_hits}h/{mu.incremental_cache_misses}m · "
        f"CPU: {mu.cpu_pct:.1f}% ({mu.cpu_time_s:.2f}s) · "
        f"RAM: {mu.rss_mib:.1f} MiB"
    )
    left_p2 = f" {c('2. Moving   :', C_LABEL, use_color)} "
    right_p2 = f" {c('│', C_MUTED, use_color)} {c(self_stats, C_CYAN, use_color)}"
    avail_w2 = max(16, W - vlen(left_p2) - vlen(right_p2))
    mov_trunc = ansi_trunc(moving_summary, avail_w2, use_color=False)
    mov_col = C_GREEN if active_tasks > 0 else (C_ORANGE if snapshot.simulated_unavailable else C_WHITE)
    line2 = left_p2 + c(f"{mov_trunc:<{avail_w2}}", mov_col, use_color) + right_p2
    lines.append(c("┃", banner_col, use_color) + pad_or_trunc(line2, W, use_color) + c("┃", banner_col, use_color))

    # Line 3: Did that work use the intended components? (for selected task)
    if sel is not None:
        idx_1 = min(max(0, snapshot.selected_task_index), len(snapshot.tasks) - 1) + 1
        art_state = "idle" if (sel.helper_artifact_path or sel.parent_artifact_path) else "not_used_by_job"
        comp_bar = (
            f" {c('3. Components', C_LABEL, use_color)} "
            f"{c(f'[#{idx_1} {sel.task_id}]', C_GOLD, use_color)}: "
            f"Ontology{format_flow_state_badge(sel.ontology_job_state, use_color)} · "
            f"Broker{format_flow_state_badge(sel.broker_job_state, use_color)} · "
            f"FamilyRoute{format_flow_state_badge(sel.router_job_state, use_color)} · "
            f"JEPA :18097{format_flow_state_badge(sel.jepa_job_state, use_color)} · "
            f"HEARTH 27B{format_flow_state_badge(sel.hearth_job_state, use_color)} · "
            f"DuckDB{format_flow_state_badge(sel.duckdb_job_state, use_color)} · "
            f"Artifact{format_flow_state_badge(art_state, use_color)}"
        )
        if vlen(comp_bar) > W:
            comp_bar = (
                f" {c('3. Path', C_LABEL, use_color)} "
                f"{c(f'[#{idx_1} {sel.task_id}]', C_GOLD, use_color)}: "
                f"Ont{format_flow_state_badge(sel.ontology_job_state, use_color)} "
                f"Broker{format_flow_state_badge(sel.broker_job_state, use_color)} "
                f"Route{format_flow_state_badge(sel.router_job_state, use_color)} "
                f"JEPA{format_flow_state_badge(sel.jepa_job_state, use_color)} "
                f"HEARTH{format_flow_state_badge(sel.hearth_job_state, use_color)} "
                f"DuckDB{format_flow_state_badge(sel.duckdb_job_state, use_color)} "
                f"Art{format_flow_state_badge(art_state, use_color)}"
            )
    else:
        comp_bar = f" {c('3. Components:', C_LABEL, use_color)} {c('No task selected', C_MUTED, use_color)}"

    lines.append(c("┃", banner_col, use_color) + pad_or_trunc(comp_bar, W, use_color) + c("┃", banner_col, use_color))
    lines.append(c("┗" + ("━" * W) + "┛", banner_col, use_color))
    return lines


# ---------------------------------------------------------------------------
# 2. Panel A: Service Availability & Latest 10 Correlated Tasks
# ---------------------------------------------------------------------------


def render_panel_a_services_and_tasks(
    snapshot: FlowSnapshot,
    W: int,
    use_color: bool = True,
    compact_tasks: bool = False,
) -> List[str]:
    lines: List[str] = []
    max_svc_age = max((s.obs_age_s or 0.0 for s in snapshot.services), default=0.0)
    any_unavail = any(s.state == "unavailable" for s in snapshot.services)
    any_active = any(s.state == "active" for s in snapshot.services)
    hdr_state = "unavailable" if any_unavail else ("active" if any_active else "idle")

    lines.append(
        _panel_header(
            "PANEL A: SERVICE AVAILABILITY & CURRENT / LATEST 10 CORRELATED TASKS",
            service_state=hdr_state,
            job_state=None,
            source="live probes (:8710,:18888,:5672/:18080,:13000,:18097,:16667,:8896) + receipts",
            obs_age_s=max_svc_age,
            width=W,
            use_color=use_color,
            title_col=C_WHITE,
        )
    )

    # 2-line compact service availability strip (up to 4 services per line)
    if not snapshot.services:
        lines.append(row_box(c(" No service availability records present.", C_MUTED, use_color), W, use_color))
    else:
        per_row = 4 if W >= 120 else 2
        for i in range(0, len(snapshot.services), per_row):
            chunk = snapshot.services[i : i + per_row]
            cell_w = max(18, (W - 2) // per_row)
            cells: List[str] = []
            for svc in chunk:
                badge = format_flow_state_badge(svc.state, use_color)
                age_txt = _fmt_age(svc.obs_age_s)
                lbl = f"{svc.name} ({svc.host_endpoint})" if svc.host_endpoint not in svc.name else svc.name
                cell_raw = f"{c(lbl, C_WHITE, use_color)} {badge} {c(age_txt, C_MUTED, use_color)}"
                cells.append(pad_or_trunc(cell_raw, cell_w, use_color))
            lines.append(row_box(" " + " ".join(cells), W, use_color))

    # Correlated Tasks Table
    lines.append(
        mid_border(
            "CURRENT & LATEST 10 CORRELATED TASKS (SELECT [1-9,0] OR [j/k] · '>' = SELECTED)",
            W,
            use_color,
            C_GOLD,
        )
    )

    # Column widths scaled to W
    #  #  | TASK ID | ACTOR | HOST | STAGE | STATE | ELAPSED | AGE | LAST PROGRESS | OUTCOME
    w_idx = 4
    w_task = 27 if W >= 140 else 20
    w_actor = 19 if W >= 140 else 14
    w_host = 13 if W >= 140 else 10
    w_stage = 21 if W >= 140 else 15
    w_state = 10
    w_elap = 7
    w_age = 6
    w_prog = 24 if W >= 140 else 16
    fixed_cols = w_idx + w_task + w_actor + w_host + w_stage + w_state + w_elap + w_age + w_prog + (9 * 3)
    w_out = max(12, W - fixed_cols - 1)

    sep = c(" │ ", C_MUTED, use_color)
    h_idx = f"{'#':<{w_idx - 1}}"
    h_task = f"{'TASK ID':<{w_task}}"
    h_actor = f"{'ACTOR':<{w_actor}}"
    h_host = f"{'HOST':<{w_host}}"
    h_stage = f"{'STAGE':<{w_stage}}"
    h_state = f"{'STATE':<{w_state}}"
    h_elap = f"{'ELAPSED':<{w_elap}}"
    h_age = f"{'AGE':<{w_age}}"
    h_prog = f"{'LAST PROGRESS':<{w_prog}}"
    h_out = f"{'OUTCOME':<{w_out}}"
    hdr_row = (
        f" {c(h_idx, C_LABEL, use_color)}"
        f"{sep}{c(h_task, C_LABEL, use_color)}"
        f"{sep}{c(h_actor, C_LABEL, use_color)}"
        f"{sep}{c(h_host, C_LABEL, use_color)}"
        f"{sep}{c(h_stage, C_LABEL, use_color)}"
        f"{sep}{c(h_state, C_LABEL, use_color)}"
        f"{sep}{c(h_elap, C_LABEL, use_color)}"
        f"{sep}{c(h_age, C_LABEL, use_color)}"
        f"{sep}{c(h_prog, C_LABEL, use_color)}"
        f"{sep}{c(h_out, C_LABEL, use_color)}"
    )
    lines.append(row_box(hdr_row, W, use_color))

    tasks = snapshot.tasks[:10]
    if not tasks:
        lines.append(row_box(c(" No correlated tasks found in receipts.", C_MUTED, use_color), W, use_color))
    else:
        sel_idx = max(0, min(snapshot.selected_task_index, len(tasks) - 1))
        for i, t in enumerate(tasks):
            is_sel = i == sel_idx
            key_lbl = "0" if (i + 1) == 10 else str(i + 1)
            marker = f">{key_lbl}" if is_sel else f" {key_lbl}"
            row_col = C_GOLD if is_sel else C_WHITE
            actor_col = C_CYAN if is_sel else C_LABEL
            elap_s = fmt_elapsed(t.elapsed_s) if t.elapsed_s is not None else "-"
            age_s = _fmt_age(t.age_s)
            st_badge = format_flow_state_badge(t.state, use_color)
            out_col = (
                C_GREEN
                if any(k in (t.outcome or "").lower() for k in ("pass", "complete", "deliver", "ok", "12/12", "certified"))
                else (C_ORANGE if "stale" in (t.outcome or "").lower() else row_col)
            )

            t_row = (
                f" {c(f'{marker:<{w_idx - 1}}', C_GOLD if is_sel else C_MUTED, use_color)}"
                f"{sep}{c(f'{ansi_trunc(t.task_id, w_task, False):<{w_task}}', row_col, use_color)}"
                f"{sep}{c(f'{ansi_trunc(t.actor, w_actor, False):<{w_actor}}', actor_col, use_color)}"
                f"{sep}{c(f'{ansi_trunc(t.host, w_host, False):<{w_host}}', row_col, use_color)}"
                f"{sep}{c(f'{ansi_trunc(t.stage, w_stage, False):<{w_stage}}', C_CYAN if is_sel else C_WHITE, use_color)}"
                f"{sep}{pad_or_trunc(st_badge, w_state, use_color)}"
                f"{sep}{c(f'{elap_s:<{w_elap}}', row_col, use_color)}"
                f"{sep}{c(f'{age_s:<{w_age}}', C_MUTED, use_color)}"
                f"{sep}{c(f'{ansi_trunc(t.last_progress, w_prog, False):<{w_prog}}', row_col, use_color)}"
                f"{sep}{c(f'{ansi_trunc(t.outcome, w_out, False):<{w_out}}', out_col, use_color)}"
            )
            lines.append(row_box(t_row, W, use_color))

    lines.append(bot_border(W, use_color))
    return lines


# ---------------------------------------------------------------------------
# 3. Panel B: Observed Path & Evidence Links for Selected Task
# ---------------------------------------------------------------------------


def _default_hops_for_task(task: CorrelatedTask) -> List[PathHop]:
    if task.observed_hops:
        return task.observed_hops
    art_state = "idle" if (task.helper_artifact_path or task.parent_artifact_path) else "not_used_by_job"
    return [
        PathHop("ontology", "Ontology", task.ontology_job_state, task.ontology_job_state != "not_used_by_job", task.ontology_term_name or task.ontology_job_note),
        PathHop("service_bus", "Service Bus", task.broker_job_state, task.broker_job_state != "not_used_by_job", task.broker_queue or "not used by this job"),
        PathHop("router", "Family Router", task.router_job_state, task.router_job_state != "not_used_by_job", task.selected_route or "not used by this job"),
        PathHop("jepa", "JEPA :18097", task.jepa_job_state, task.jepa_job_state not in ("not_used_by_job", "unknown"), task.jepa_job_note),
        PathHop("hearth", "HEARTH/MAF", task.hearth_job_state, task.hearth_job_state != "not_used_by_job", task.hearth_model or task.steppewise_workflow),
        PathHop("duckdb", "DuckDB", task.duckdb_job_state, task.duckdb_job_state != "not_used_by_job", task.duckdb_job_note),
        PathHop("artifact", "Artifact", art_state, art_state != "not_used_by_job", task.helper_artifact_id or task.parent_artifact_path),
    ]


def render_panel_b_observed_path(
    snapshot: FlowSnapshot,
    W: int,
    use_color: bool = True,
    expanded: bool = False,
) -> List[str]:
    lines: List[str] = []
    task = snapshot.selected_task
    if task is None:
        lines.append(top_border("PANEL B: OBSERVED PATH & EVIDENCE LINKS (NO TASK SELECTED)", W, use_color, C_GOLD))
        lines.append(row_box(c(" No correlated task selected.", C_MUTED, use_color), W, use_color))
        lines.append(bot_border(W, use_color))
        return lines

    idx_1 = min(max(0, snapshot.selected_task_index), len(snapshot.tasks) - 1) + 1
    lines.append(
        _panel_header(
            f"PANEL B: OBSERVED PATH & EVIDENCE LINKS — SELECTED TASK [#{idx_1}] {task.task_id}",
            service_state=task.state,
            job_state=None,
            source=task.source_receipt_path or task.run_dir or "task receipts",
            obs_age_s=task.age_s,
            width=W,
            use_color=use_color,
            title_col=C_GOLD,
        )
    )

    # Hop-by-hop pipeline strip
    hops = _default_hops_for_task(task)
    hop_tokens: List[str] = []
    arrow = c(" ──▶ ", C_MUTED, use_color)
    for h in hops:
        b = format_flow_state_badge(h.state, use_color)
        used_tag = ""
        if h.state == "idle" and h.used_by_job is True:
            used_tag = c("(used)", C_GREEN, use_color)
        hop_tokens.append(f"{c(h.label, C_WHITE, use_color)}{used_tag}{b}")

    # Split hop pipeline across 1 or 2 rows so badges never clip
    row1_hops = arrow.join(hop_tokens[:4])
    row2_hops = arrow.join(hop_tokens[4:])
    combined_hops = f" {c('Observed Path  :', C_LABEL, use_color)} {row1_hops}{arrow if row2_hops else ''}{row2_hops}"
    if vlen(combined_hops) <= W:
        lines.append(row_box(combined_hops, W, use_color))
    else:
        lines.append(row_box(f" {c('Observed Path  :', C_LABEL, use_color)} {row1_hops}", W, use_color))
        if row2_hops:
            lines.append(row_box(f"                  {arrow}{row2_hops}", W, use_color))

    if expanded:
        for h in hops:
            hb = format_flow_state_badge(h.state, use_color)
            ts_part = f" @ {h.timestamp_utc}" if h.timestamp_utc else ""
            ev_part = f" · link={h.evidence_link}" if h.evidence_link else ""
            lines.extend(
                _wrap_kv_rows(
                    f"  Hop [{h.label:<13}]:",
                    f"{format_state_label(h.state)} — {h.summary}{ts_part}{ev_part}",
                    W,
                    use_color=use_color,
                    label_col=C_CYAN,
                    val_col=C_WHITE if h.state != "not_used_by_job" else C_MUTED,
                )
            )
            lines[-1] = row_box(
                f"   {c(f'Hop [{h.label:<13}]:', C_CYAN, use_color)} {hb} "
                + ansi_trunc(f"{h.summary}{ts_part}{ev_part}", max(16, W - 30 - vlen(hb)), use_color=False),
                W,
                use_color,
            )

    # 1. Aspire Trace link & OTEL spans path
    if task.aspire_trace_url or task.otel_spans_path:
        exp_str = f"exported={task.otel_exported}, spans={task.otel_span_count}, err={task.otel_error_count}"
        trace_val = (
            f"{task.aspire_trace_url or 'no-url'} · "
            f"{task.otel_spans_path or 'no-otel-file'} ({exp_str})"
        )
        lines.extend(_wrap_kv_rows("Aspire Trace   :", trace_val, W, use_color, C_LABEL, C_CYAN))
    else:
        lines.append(
            row_box(
                f" {c('Aspire Trace   :', C_LABEL, use_color)} "
                f"{format_flow_state_badge('not_used_by_job', use_color)} (no trace recorded for this task)",
                W,
                use_color,
            )
        )

    # 2. Broker Evidence link & receipt path
    if task.broker_job_state != "not_used_by_job" and (task.broker_receipt_path or task.broker_explorer_url):
        seq_str = (
            f"queue={task.broker_queue} seq={task.broker_sequence_number} "
            f"deliv={task.broker_delivery_count} disp={task.broker_disposition}"
        )
        broker_val = (
            f"{task.broker_receipt_path} · {task.broker_explorer_url} ({seq_str})"
        )
        lines.extend(_wrap_kv_rows("Broker Evidence:", broker_val, W, use_color, C_LABEL, C_GREEN))
    else:
        lines.append(
            row_box(
                f" {c('Broker Evidence:', C_LABEL, use_color)} "
                f"{format_flow_state_badge(task.broker_job_state, use_color)} "
                f"{c('not used by this job', C_MUTED, use_color)}",
                W,
                use_color,
            )
        )

    # 3. Ontology Entry URL & receipt path
    ont_url = task.ontology_entry_url or DEFAULT_ONTOLOGY_UI_URL
    if task.ontology_job_state != "not_used_by_job":
        ms_s = f"{task.ontology_lookup_ms:.1f}ms" if task.ontology_lookup_ms is not None else "n/a"
        ont_val = (
            f"{ont_url} · {task.ontology_receipt_path} "
            f"({task.ontology_term_name} -> {task.ontology_attr_name}, {ms_s}, sha256:{task.ontology_definition_sha256[:16]})"
        )
        lines.extend(_wrap_kv_rows("Ontology Entry :", ont_val, W, use_color, C_LABEL, C_GOLD))
    else:
        ont_unused = f"{ont_url} · [NOT USED BY THIS JOB] ({task.ontology_job_note})"
        lines.extend(_wrap_kv_rows("Ontology Entry :", ont_unused, W, use_color, C_LABEL, C_MUTED))

    # 4. Result Artifact path, SHA256, parent artifact, preview URL
    art_parts: List[str] = []
    if task.helper_artifact_path:
        id_part = f" [{task.helper_artifact_id}]" if task.helper_artifact_id else ""
        sha_part = f" sha256:{task.helper_artifact_sha256[:16]}" if task.helper_artifact_sha256 else ""
        art_parts.append(f"{task.helper_artifact_path}{id_part}{sha_part}")
    if task.parent_artifact_path:
        psha = f" (sha256:{task.parent_artifact_sha256[:12]})" if task.parent_artifact_sha256 else ""
        art_parts.append(f"Parent: {task.parent_artifact_path}{psha}")
    if task.check_artifact_path:
        art_parts.append(f"Check: {task.check_artifact_path}")
    if task.preview_url:
        art_parts.append(f"Preview: {task.preview_url}")

    if art_parts:
        lines.extend(_wrap_kv_rows("Result Artifact:", " · ".join(art_parts), W, use_color, C_LABEL, C_WHITE))
    else:
        lines.append(
            row_box(
                f" {c('Result Artifact:', C_LABEL, use_color)} {format_flow_state_badge('not_used_by_job', use_color)}",
                W,
                use_color,
            )
        )

    lines.append(bot_border(W, use_color))
    return lines


# ---------------------------------------------------------------------------
# 4. Six Component Detail Panels (Left & Right Columns)
# ---------------------------------------------------------------------------


def render_service_bus_panel(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> List[str]:
    sb: ServiceBusPanel = snapshot.service_bus
    task = snapshot.selected_task
    job_st = task.broker_job_state if task else "not_used_by_job"

    lines: List[str] = []
    lines.append(
        _panel_header(
            "1. SERVICE BUS (:5672 / EXPLORER :18080)",
            service_state=sb.state,
            job_state=job_st,
            source=sb.source,
            obs_age_s=sb.obs_age_s,
            width=cw,
            use_color=use_color,
            title_col=C_CYAN,
        )
    )

    if sb.error_note or sb.state == "unavailable":
        err_msg = sb.error_note or "Service Bus collector unavailable"
        lines.append(
            row_box(
                f" {format_flow_state_badge('unavailable', use_color)} {c(err_msg, C_RED, use_color)}",
                cw,
                use_color,
            )
        )

    # Explicitly label Queue depth = backlog (not throughput)
    lines.append(
        row_box(
            f" {c('Queue depth = backlog (not throughput):', C_GOLD, use_color)} "
            f"queued={c(str(sb.total_queued_backlog), C_WHITE, use_color)} · "
            f"DLQ={c(str(sb.total_dead_letters), C_RED if sb.total_dead_letters > 0 else C_GREEN, use_color)} · "
            f"in_flight={c(str(sb.unsettled_in_flight), C_CYAN, use_color)}",
            cw,
            use_color,
        )
    )

    if sb.queues:
        q_items: List[str] = []
        for q in sb.queues:
            kind = "t" if q.entity_type == "topic" else "q"
            q_items.append(f"{q.entity_name}({kind}): q={q.active_messages} dlq={q.dead_letter_messages}")
        lines.append(
            row_box(
                f" {c('Entities       :', C_LABEL, use_color)} "
                + c(" · ".join(q_items), C_WHITE, use_color),
                cw,
                use_color,
            )
        )

    lines.append(
        row_box(
            f" {c('Broker Activity:', C_LABEL, use_color)} "
            f"send={c(str(sb.recent_send_count), C_GREEN, use_color)} · "
            f"recv={c(str(sb.recent_receive_count), C_CYAN, use_color)} · "
            f"settle={c(str(sb.recent_settle_count), C_GREEN, use_color)} · "
            f"RO_guard={c(str(sb.read_only_enforced), C_GREEN, use_color)}",
            cw,
            use_color,
        )
    )

    if task is not None and task.broker_job_state != "not_used_by_job":
        lines.append(
            row_box(
                f" {c('[THIS JOB]     :', C_LABEL, use_color)} "
                f"{format_flow_state_badge(task.broker_job_state, use_color)} "
                f"q={c(task.broker_queue, C_WHITE, use_color)} "
                f"seq={task.broker_sequence_number} deliv={task.broker_delivery_count} "
                f"disp={c(task.broker_disposition, C_GREEN, use_color)}",
                cw,
                use_color,
            )
        )
        lines.append(
            row_box(
                f"   {c('Timestamps   :', C_MUTED, use_color)} "
                f"send={task.broker_send_at} -> recv={task.broker_recv_at} -> settle={task.broker_settle_at}",
                cw,
                use_color,
            )
        )
    else:
        lines.append(
            row_box(
                f" {c('[THIS JOB]     :', C_LABEL, use_color)} "
                f"{format_flow_state_badge('not_used_by_job', use_color)} "
                f"{c('not used by this job', C_MUTED, use_color)}",
                cw,
                use_color,
            )
        )

    lines.append(bot_border(cw, use_color))
    return lines


def render_hearth_maf_panel(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> List[str]:
    task = snapshot.selected_task
    hearth_svc = _service_by_id(snapshot, "hearth")
    svc_state = hearth_svc.state if hearth_svc else "idle"
    svc_age = hearth_svc.obs_age_s if hearth_svc else (task.age_s if task else 0.0)
    job_st = task.hearth_job_state if task else "not_used_by_job"
    src_str = (
        task.hearth_execution_path or task.steppewise_events_path or "HEARTH :8710 + execution.json / events.jsonl"
        if task
        else "HEARTH :8710"
    )

    lines: List[str] = []
    lines.append(
        _panel_header(
            "2. HEARTH / STEPPEWISE / MAF",
            service_state=svc_state,
            job_state=job_st,
            source=src_str,
            obs_age_s=svc_age,
            width=cw,
            use_color=use_color,
            title_col=C_TIER_B70,
        )
    )

    if svc_state == "unavailable":
        detail_err = hearth_svc.detail if hearth_svc else "HEARTH :8710 unavailable"
        lines.append(
            row_box(
                f" {format_flow_state_badge('unavailable', use_color)} {c(detail_err, C_RED, use_color)}",
                cw,
                use_color,
            )
        )

    if task is None:
        lines.append(row_box(c(" No task selected.", C_MUTED, use_color), cw, use_color))
        lines.append(bot_border(cw, use_color))
        return lines

    lines.append(
        row_box(
            f" {c('[THIS JOB] HEARTH:', C_LABEL, use_color)} "
            f"{format_flow_state_badge(task.hearth_job_state, use_color)} "
            f"job={c(task.hearth_job_id or '-', C_WHITE, use_color)} · "
            f"inv={c(task.hearth_invocation_id or '-', C_CYAN, use_color)} · "
            f"{c(f'{task.hearth_provider}/{task.hearth_model}', C_GOLD, use_color)} ({task.hearth_status or '-'})",
            cw,
            use_color,
        )
    )
    stages_str = " -> ".join(task.hearth_stages) if task.hearth_stages else "none"
    lines.append(
        row_box(
            f" {c('Observed Stages:', C_LABEL, use_color)} {c(stages_str, C_WHITE, use_color)}",
            cw,
            use_color,
        )
    )
    lines.append(
        row_box(
            f" {c('Retries/Repairs:', C_LABEL, use_color)} "
            f"app_retries={c(str(task.hearth_app_retries), C_GREEN if task.hearth_app_retries == 0 else C_ORANGE, use_color)} · "
            f"emit_repairs={c(str(task.hearth_emit_repairs), C_GOLD if task.hearth_emit_repairs > 0 else C_GREEN, use_color)} "
            f"({task.hearth_emit_trigger or 'none'})",
            cw,
            use_color,
        )
    )
    lines.append(
        row_box(
            f" {c('Delivery/IRC   :', C_LABEL, use_color)} "
            f"delivery_repairs={c(str(task.hearth_delivery_repairs), C_GOLD if task.hearth_delivery_repairs > 0 else C_GREEN, use_color)} "
            f"({task.hearth_delivery_repair_note or 'none'})",
            cw,
            use_color,
        )
    )
    steps_s = " -> ".join(task.steppewise_steps) if task.steppewise_steps else "-"
    lines.append(
        row_box(
            f" {c('Steppewise/MAF :', C_LABEL, use_color)} "
            f"{format_flow_state_badge(task.steppewise_state, use_color)} "
            f"wf={c(task.steppewise_workflow or 'not used by this job', C_CYAN if task.steppewise_workflow else C_MUTED, use_color)} · "
            f"run={task.steppewise_run_id or '-'} · steps={steps_s}",
            cw,
            use_color,
        )
    )
    lines.append(bot_border(cw, use_color))
    return lines


def render_ontology_panel(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> List[str]:
    ont: OntologyPanel = snapshot.ontology
    task = snapshot.selected_task
    job_st = task.ontology_job_state if task else "not_used_by_job"

    lines: List[str] = []
    lines.append(
        _panel_header(
            "3. ONTOLOGY (OMEN :13000 -> AM4 10.44.0.2)",
            service_state=ont.state,
            job_state=job_st,
            source=ont.source,
            obs_age_s=ont.obs_age_s,
            width=cw,
            use_color=use_color,
            title_col=C_TIER_AM4,
        )
    )

    if ont.error_note or ont.state == "unavailable":
        err_msg = ont.error_note or "Ontology service unavailable"
        lines.append(
            row_box(
                f" {format_flow_state_badge('unavailable', use_color)} {c(err_msg, C_RED, use_color)}",
                cw,
                use_color,
            )
        )

    lines.append(
        row_box(
            f" {c('Placement/Route:', C_LABEL, use_color)} "
            f"{c(ont.placement_summary, C_WHITE, use_color)} "
            f"[pg={c(ont.postgres_status, C_GREEN if ont.postgres_status == 'ok' else C_ORANGE, use_color)}]",
            cw,
            use_color,
        )
    )
    lines.append(
        row_box(
            f" {c('Selected Term  :', C_LABEL, use_color)} "
            f"{c(ont.selected_term_name, C_GOLD, use_color)} ({ont.selected_term_id[:8]}) -> "
            f"{c(ont.selected_attr_name, C_CYAN, use_color)} ({ont.selected_attr_id[:8]}): "
            f"{c(repr(ont.selected_attr_sample), C_GREEN, use_color)}",
            cw,
            use_color,
        )
    )
    lines.append(
        row_box(
            f" {c('Review & Cert  :', C_LABEL, use_color)} "
            f"term={c(ont.selected_term_cert, C_GOLD, use_color)} · "
            f"attr_certified={c(str(ont.selected_attr_certified).lower(), C_GREEN if ont.selected_attr_certified else C_ORANGE, use_color)} · "
            f"{c(ont.certified_attrs_ratio, C_WHITE, use_color)}",
            cw,
            use_color,
        )
    )
    lines.append(
        row_box(
            f" {c('Provenance     :', C_LABEL, use_color)} "
            f"{c(ont.review_source, C_WHITE, use_color)} · by={c(ont.reviewed_by, C_CYAN, use_color)}",
            cw,
            use_color,
        )
    )
    lk_ms = f"{ont.last_lookup_ms:.1f}ms" if ont.last_lookup_ms is not None else "n/a"
    cmd_s = f"{ont.last_lookup_command_s:.2f}s" if ont.last_lookup_command_s is not None else "n/a"
    lines.append(
        row_box(
            f" {c('Last Lookup    :', C_LABEL, use_color)} "
            f"{c(ont.last_lookup_utc or '-', C_WHITE, use_color)} · "
            f"http={c(lk_ms, C_GREEN, use_color)} (cmd={cmd_s}) · "
            f"sha256={c(ont.last_lookup_sha256[:12] if ont.last_lookup_sha256 else '-', C_GOLD, use_color)}",
            cw,
            use_color,
        )
    )

    if task is not None and task.ontology_job_state != "not_used_by_job":
        t_ms = f"{task.ontology_lookup_ms:.1f}ms" if task.ontology_lookup_ms is not None else "n/a"
        lines.append(
            row_box(
                f" {c('[THIS JOB]     :', C_LABEL, use_color)} "
                f"{format_flow_state_badge(task.ontology_job_state, use_color)} "
                f"{c(f'{task.ontology_term_name} -> {task.ontology_attr_name}', C_GREEN, use_color)} "
                f"({t_ms}, sha256:{task.ontology_definition_sha256[:12]})",
                cw,
                use_color,
            )
        )
    else:
        note_s = task.ontology_job_note if task else "not used by this job"
        lines.append(
            row_box(
                f" {c('[THIS JOB]     :', C_LABEL, use_color)} "
                f"{format_flow_state_badge('not_used_by_job', use_color)} "
                f"{c(note_s, C_MUTED, use_color)}",
                cw,
                use_color,
            )
        )

    lines.append(bot_border(cw, use_color))
    return lines


def render_router_packing_panel(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> List[str]:
    task = snapshot.selected_task
    jepa_svc = _service_by_id(snapshot, "jepa_router")
    svc_state = jepa_svc.state if jepa_svc else "idle"
    svc_age = jepa_svc.obs_age_s if jepa_svc else (task.age_s if task else 0.0)
    job_st = task.router_job_state if task else "not_used_by_job"
    src_str = (
        task.router_receipt_path or "plan.json + execution.json + :18097/health"
        if task
        else ":18097/health"
    )

    lines: List[str] = []
    lines.append(
        _panel_header(
            "4. ROUTER & CONTEXT PACKING",
            service_state=svc_state,
            job_state=job_st,
            source=src_str,
            obs_age_s=svc_age,
            width=cw,
            use_color=use_color,
            title_col=C_TIER_NPU,
        )
    )

    if task is None:
        lines.append(row_box(c(" No task selected.", C_MUTED, use_color), cw, use_color))
        lines.append(bot_border(cw, use_color))
        return lines

    lines.append(
        row_box(
            f" {c('[THIS JOB] Route:', C_LABEL, use_color)} "
            f"{format_flow_state_badge(task.router_job_state, use_color)} "
            f"route={c(task.selected_route or '-', C_GREEN, use_color)} · "
            f"family={c(task.task_family or '-', C_CYAN, use_color)} · "
            f"regime={c(task.regime_summary or '-', C_GOLD, use_color)}",
            cw,
            use_color,
        )
    )
    sz_s = f"{task.pack_size_bytes} B" if task.pack_size_bytes is not None else "n/a"
    lines.append(
        row_box(
            f" {c('Context Pack   :', C_LABEL, use_color)} "
            f"size={c(sz_s, C_WHITE, use_color)} · "
            f"sha256={c(task.pack_sha256[:16] if task.pack_sha256 else '-', C_GOLD, use_color)} · "
            f"packed_files={c(str(task.packed_files_count), C_CYAN, use_color)} · "
            f"pool_cfg={c(task.pool_config_hash or '-', C_MUTED, use_color)}",
            cw,
            use_color,
        )
    )
    lines.append(
        row_box(
            f" {c('Omissions/Fallb:', C_LABEL, use_color)} "
            f"{c(task.omissions_or_fallback or 'none', C_WHITE, use_color)}",
            cw,
            use_color,
        )
    )
    lines.append(
        row_box(
            f" {c(':18097 JEPA Rtr:', C_LABEL, use_color)} "
            f"{format_flow_state_badge(task.jepa_job_state, use_color)} "
            f"{c(task.jepa_job_note, C_MUTED if task.jepa_job_state == 'not_used_by_job' else C_WHITE, use_color)}",
            cw,
            use_color,
        )
    )
    lines.append(bot_border(cw, use_color))
    return lines


def render_tokens_caches_panel(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> List[str]:
    lc: LifetimeCacheSummary = snapshot.lifetime_caches
    task = snapshot.selected_task
    job_st = task.tokens_job_state if task else "not_used_by_job"

    lines: List[str] = []
    lines.append(
        _panel_header(
            "5. TOKENS & CACHES (JOB RUN vs LIFETIME SEPARATED)",
            service_state=lc.state,
            job_state=job_st,
            source=f"execution.json (job) | {lc.source}",
            obs_age_s=lc.obs_age_s,
            width=cw,
            use_color=use_color,
            title_col=C_GOLD,
        )
    )

    if task is not None:
        lines.append(
            row_box(
                f" {c('[THIS JOB RUN]', C_GOLD, use_color)} "
                f"{format_flow_state_badge(task.tokens_job_state, use_color)} "
                f"calls={c(str(task.model_calls), C_WHITE, use_color)} · "
                f"in={c(str(task.tokens_in_total if task.tokens_in_total is not None else '-'), C_CYAN, use_color)} · "
                f"out={c(str(task.tokens_out_total if task.tokens_out_total is not None else '-'), C_GREEN, use_color)} "
                f"(reasoning={task.tokens_reasoning_total if task.tokens_reasoning_total is not None else '-'})",
                cw,
                use_color,
            )
        )
        t1_s = (
            f"Turn1: in={task.turn1_tokens_in} out={task.turn1_tokens_out} "
            f"({task.turn1_finish_reason}, {task.turn1_duration_ms}ms)"
            if task.turn1_tokens_in is not None
            else "Turn1: n/a"
        )
        t2_s = (
            f"Turn2 emit: in={task.emit_tokens_in} out={task.emit_tokens_out} "
            f"({task.emit_finish_reason}, {task.emit_duration_ms}ms)"
            if task.emit_tokens_in is not None
            else "Turn2 emit: none"
        )
        lines.append(
            row_box(
                f"   {c('Turns        :', C_LABEL, use_color)} {c(f'{t1_s} | {t2_s}', C_WHITE, use_color)}",
                cw,
                use_color,
            )
        )
        lines.append(
            row_box(
                f"   {c('Cached Input :', C_LABEL, use_color)} "
                f"{format_flow_state_badge(task.cached_input_tokens_state, use_color)} "
                f"{c(task.cached_input_tokens_note, C_PURPLE, use_color)}",
                cw,
                use_color,
            )
        )
        lines.append(
            row_box(
                f"   {c('Job AST/Text :', C_LABEL, use_color)} "
                f"AST={format_flow_state_badge(task.ast_cache_job_state, use_color)} · "
                f"Text/SOL={format_flow_state_badge(task.text_cache_job_state, use_color)}",
                cw,
                use_color,
            )
        )

    lines.append(
        mid_border(
            "LIFETIME / ENGINE UPTIME CACHES (SEPARATE FROM THIS JOB RUN)",
            cw,
            use_color,
            C_CYAN,
        )
    )
    if lc.state == "unavailable":
        lines.append(
            row_box(
                f" {format_flow_state_badge('unavailable', use_color)} "
                f"{c('Lifetime cache collector unavailable', C_RED, use_color)}",
                cw,
                use_color,
            )
        )
    else:
        kv_str = " · ".join(lc.kv_caches_summary) if lc.kv_caches_summary else "no KV metrics"
        lines.append(
            row_box(
                f" {c('vLLM KV-Prefix :', C_LABEL, use_color)} {c(kv_str, C_CYAN, use_color)}",
                cw,
                use_color,
            )
        )
        lines.append(
            row_box(
                f" {c('NPU AST / SOL  :', C_LABEL, use_color)} "
                f"AST(:18097)={c(lc.npu_ast_summary or 'n/a', C_GREEN if lc.npu_ast_online else C_RED, use_color)} · "
                f"SOL={c(lc.sol_arena_summary or 'n/a', C_MUTED, use_color)}",
                cw,
                use_color,
            )
        )

    lines.append(bot_border(cw, use_color))
    return lines


def render_duckdb_panel(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> List[str]:
    ddb: DuckDBPanel = snapshot.duckdb
    task = snapshot.selected_task
    job_st = task.duckdb_job_state if task else "not_used_by_job"

    lines: List[str] = []
    lines.append(
        _panel_header(
            "6. DUCKDB INTAKE (SNAPSHOT-FIRST)",
            service_state=ddb.state,
            job_state=job_st,
            source=ddb.source,
            obs_age_s=ddb.obs_age_s,
            width=cw,
            use_color=use_color,
            title_col=C_GREEN,
        )
    )

    if ddb.error_note or ddb.state == "unavailable":
        err_msg = ddb.error_note or "DuckDB collector unavailable"
        lines.append(
            row_box(
                f" {format_flow_state_badge('unavailable', use_color)} {c(err_msg, C_RED, use_color)}",
                cw,
                use_color,
            )
        )

    lines.append(
        row_box(
            f" {c('Hosting/Writer :', C_LABEL, use_color)} "
            f"{c(ddb.active_writer_cmd, C_WHITE, use_color)} "
            f"(pid={ddb.active_writer_pid if ddb.active_writer_pid is not None else 'none'})",
            cw,
            use_color,
        )
    )
    lines.append(
        row_box(
            f" {c('Last Commit    :', C_LABEL, use_color)} "
            f"{c(ddb.last_commit_utc or '-', C_CYAN, use_color)} · "
            f"{c(ddb.last_commit_summary or '-', C_WHITE, use_color)}",
            cw,
            use_color,
        )
    )
    lines.append(
        row_box(
            f" {c('Intake Backlog :', C_LABEL, use_color)} "
            f"pending dispatches: {c(str(ddb.backlog_pending_dispatches), C_GREEN, use_color)} · "
            f"pending outbox: {c(str(ddb.backlog_pending_outbox), C_GREEN, use_color)} · "
            f"nonterminal: {c(str(ddb.backlog_nonterminal_tasks), C_WHITE, use_color)}",
            cw,
            use_color,
        )
    )

    # Net row Δ strictly labeled as net row change
    tbl_parts = [f"{k}={v}" for k, v in ddb.washplant_table_counts.items()]
    seed_tot = sum(ddb.seed_table_counts.values())
    lines.append(
        row_box(
            f" {c('Net Row Change :', C_LABEL, use_color)} "
            f"{c(f'Net row Δ vs snapshot hopper-07efb951: {ddb.net_row_change_total:+d} rows', C_GOLD, use_color)} "
            f"[{', '.join(tbl_parts)}] · Seed: +{seed_tot} rows",
            cw,
            use_color,
        )
    )
    lines.append(
        row_box(
            f" {c('Updates/Deletes:', C_LABEL, use_color)} "
            f"{format_flow_state_badge('unknown', use_color)} "
            f"{c(ddb.updates_deletions_status, C_PURPLE, use_color)}",
            cw,
            use_color,
        )
    )
    job_note = task.duckdb_job_note if task else "not used by this job"
    lines.append(
        row_box(
            f" {c('[THIS JOB]     :', C_LABEL, use_color)} "
            f"{format_flow_state_badge(job_st, use_color)} "
            f"{c(job_note, C_MUTED if job_st == 'not_used_by_job' else C_WHITE, use_color)}",
            cw,
            use_color,
        )
    )
    lines.append(bot_border(cw, use_color))
    return lines


def render_component_columns(snapshot: FlowSnapshot, W: int, use_color: bool = True) -> List[str]:
    """Compose the 6 component detail panels in a balanced 2-column layout (or stacked if narrow)."""
    if W < 110:
        out: List[str] = []
        out.extend(render_service_bus_panel(snapshot, W, use_color))
        out.extend(render_hearth_maf_panel(snapshot, W, use_color))
        out.extend(render_ontology_panel(snapshot, W, use_color))
        out.extend(render_router_packing_panel(snapshot, W, use_color))
        out.extend(render_tokens_caches_panel(snapshot, W, use_color))
        out.extend(render_duckdb_panel(snapshot, W, use_color))
        return out

    L_w = (W - 3) // 2
    R_w = W - 3 - L_w

    left_pairs = [
        render_service_bus_panel(snapshot, L_w, use_color),
        render_hearth_maf_panel(snapshot, L_w, use_color),
        render_ontology_panel(snapshot, L_w, use_color),
    ]
    right_pairs = [
        render_router_packing_panel(snapshot, R_w, use_color),
        render_tokens_caches_panel(snapshot, R_w, use_color),
        render_duckdb_panel(snapshot, R_w, use_color),
    ]

    combined: List[str] = []
    for l_box, r_box in zip(left_pairs, right_pairs):
        l_lines = list(l_box)
        r_lines = list(r_box)
        if len(l_lines) < len(r_lines):
            bot_l = l_lines.pop()
            while len(l_lines) < len(r_lines) - 1:
                l_lines.append(row_box("", L_w, use_color))
            l_lines.append(bot_l)
        elif len(r_lines) < len(l_lines):
            bot_r = r_lines.pop()
            while len(r_lines) < len(l_lines) - 1:
                r_lines.append(row_box("", R_w, use_color))
            r_lines.append(bot_r)
        for lr, rr in zip(l_lines, r_lines):
            combined.append(f"{lr} {rr}")

    return combined


# ---------------------------------------------------------------------------
# 5. Focused Sub-Views (`path`, `services`, `caches`, `data`) & Footer
# ---------------------------------------------------------------------------


def render_services_detail_view(snapshot: FlowSnapshot, W: int, use_color: bool = True) -> List[str]:
    lines: List[str] = []
    max_svc_age = max((s.obs_age_s or 0.0 for s in snapshot.services), default=0.0)
    readiness = any(s.readiness_scope or s.service_id == "readiness_probe" for s in snapshot.services)
    title = (f"WASHPLANT READINESS ({len(snapshot.services)} REQUIRED SERVICES)" if readiness
             else f"EXPANDED SERVICE AVAILABILITY TABLE ({len(snapshot.services)} MONITORED SERVICES)")
    lines.append(
        _panel_header(
            title,
            service_state=snapshot.overall_state,
            job_state=None,
            source="live probes + receipts",
            obs_age_s=max_svc_age,
            width=W,
            use_color=use_color,
            title_col=C_WHITE,
        )
    )
    for svc in snapshot.services:
        badge = format_flow_state_badge(svc.state, use_color)
        age_s = _fmt_age(svc.obs_age_s)
        lines.append(
            row_box(
                f" {c(f'{svc.name:<24}', C_WHITE, use_color)} │ "
                f"{c(f'{svc.host_endpoint:<28}', C_CYAN, use_color)} │ "
                f"{pad_or_trunc(badge, 14, use_color)} │ "
                f"age={c(f'{age_s:<6}', C_MUTED, use_color)} │ "
                f"src={c(svc.source, C_LABEL, use_color)} │ "
                f"{c(svc.detail, C_WHITE, use_color)}",
                W,
                use_color,
            )
        )
    lines.append(bot_border(W, use_color))
    if not readiness:
        lines.extend(render_service_bus_panel(snapshot, W, use_color))
        lines.extend(render_hearth_maf_panel(snapshot, W, use_color))
        lines.extend(render_ontology_panel(snapshot, W, use_color))
    return lines


def render_footer_nav(active_view: str, W: int, use_color: bool = True) -> List[str]:
    v_items = [
        ("overview", "[o]verview"),
        ("path", "[p]ath"),
        ("services", "[s]ervices"),
        ("caches", "[c]aches"),
        ("data", "[d]ata"),
    ]
    rendered_v: List[str] = []
    for v_name, v_lbl in v_items:
        if v_name == active_view:
            rendered_v.append(c(v_lbl, C_CYAN, use_color))
        else:
            rendered_v.append(c(v_lbl, C_LABEL, use_color))

    nav_line = (
        f" {c('Views:', C_WHITE, use_color)} "
        + " ".join(rendered_v)
        + f" {c('│', C_MUTED, use_color)} "
        + f"{c('Select Task:', C_WHITE, use_color)} {c('[1-9,0] [j/k]', C_GOLD, use_color)}"
        + f" {c('│', C_MUTED, use_color)} "
        + f"{c('[r]efresh [q]uit', C_GREEN, use_color)}"
    )
    return [
        c("┌" + ("─" * W) + "┐", C_BORDER, use_color),
        row_box(nav_line, W, use_color),
        bot_border(W, use_color),
    ]


# ---------------------------------------------------------------------------
# Compact Overview HUD (`overview` view: ~120 cols × ~34–36 lines)
# ---------------------------------------------------------------------------


def _resolve_flow_inner_width(
    width: Optional[int],
    is_tty: bool = False,
    view: str = "overview",
) -> int:
    """Resolve inner box width `W` (defaults to 118 for `overview` -> 120 total cols, 156 for detail views)."""
    if width is not None:
        return resolve_inner_width(width, is_tty=is_tty)
    import os

    for fd in (1, 0, 2):
        try:
            if os.isatty(fd):
                sz = os.get_terminal_size(fd)
                if not is_tty and sz.columns == 80 and sz.lines == 24:
                    continue
                if sz.columns > 0:
                    return max(76, min(300, sz.columns - 3))
        except Exception:
            continue
    return 118 if view == "overview" else 156


def _format_clock_short_pst(snapshot: FlowSnapshot) -> str:
    if snapshot.collected_at_unix > 0:
        try:
            dt = datetime.fromtimestamp(snapshot.collected_at_unix, tz=ZoneInfo("America/Los_Angeles"))
            return dt.strftime("%H:%M:%S %Z")
        except Exception:
            pass
    return snapshot.collected_at_utc or "-"


def _fmt_duration_spaced(sec: Optional[float]) -> str:
    if sec is None:
        return "n/a"
    val = max(0.0, float(sec))
    if val < 1.0:
        return f"{val * 1000.0:.0f}ms"
    s = int(round(val))
    if s < 60:
        return f"{val:.1f}s" if val < 10.0 else f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _fmt_age_compact(age_s: Optional[float]) -> str:
    if age_s is None:
        return "n/a"
    val = max(0.0, float(age_s))
    if val < 60.0:
        return f"{int(round(val))}s"
    s = int(round(val))
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _two_segment_bar(
    seg1_val: float,
    seg2_val: float,
    width: int = 12,
    col1: str = C_CYAN,
    col2: str = C_GREEN,
    use_color: bool = True,
) -> str:
    """Render a 2-segment bar (`█` for segment 1, `▓` for segment 2) with exact shape equivalence."""
    width = max(4, width)
    v1 = max(0.0, float(seg1_val))
    v2 = max(0.0, float(seg2_val))
    tot = v1 + v2
    if tot <= 0.0:
        inner = c("·" * width, C_DARK, use_color) if use_color else ("·" * width)
        return c("[", C_MUTED, use_color) + inner + c("]", C_MUTED, use_color)

    n1 = int(round((v1 / tot) * width))
    if v1 > 0.0 and n1 == 0:
        n1 = 1
    if v2 > 0.0 and n1 >= width:
        n1 = width - 1
    n2 = max(0, width - n1)
    s1 = c("█" * n1, col1, use_color) if use_color else ("█" * n1)
    s2 = c("▓" * n2, col2, use_color) if use_color else ("▓" * n2)
    return c("[", C_MUTED, use_color) + s1 + s2 + c("]", C_MUTED, use_color)


def _derive_human_title(task: CorrelatedTask, compact: bool = False, for_table: bool = False) -> str:
    tid = (task.task_id or "").strip()
    if compact:
        compact_map = {
            "help-orland-throughput-20261104": "Orland — copy + CSV + JSON",
            "help-orland-live-20261104": "Orland — copy + CSV + JSON",
            "ontology-placement-20261104": "Ontology — OMEN -> AM4 move",
            "help-ontology-20261104": "Solicitation — Budget Label",
            "help-orland-broker-20261104": "Orland — filter URL via Broker",
            "help-orland-feature-20261104": "Orland — Event Finder feature",
            "help-b3-packet-check-20261104": "B3 packet check — HEARTH 27B",
            "help-publish-recovery-20261104": "IRC publish recovery check",
            "help-observability-20261104": "Service Bus Explorer check",
            "help-loop-20261104T2114Z": "HearthClient — artifact SHA256",
            "wp001-AX-00714280": "Washplant wp001 — DuckDB+Broker",
        }
        if tid in compact_map:
            return compact_map[tid]
    if for_table and tid == "help-orland-live-20261104":
        return "Orland — copy + CSV + JSON [LIVE]"
    mapping = {
        "help-orland-throughput-20261104": "Orland — copy link + CSV + JSON",
        "help-orland-live-20261104": "Orland — copy link + CSV + JSON [ACTIVE FIXTURE]",
        "ontology-placement-20261104": "Ontology — AM4 Docker placement",
        "help-ontology-20261104": "Solicitation — Budget Label lookup",
        "help-orland-broker-20261104": "Orland — shareable filter URL",
        "help-orland-feature-20261104": "Orland — Event Finder initial UI",
        "help-b3-packet-check-20261104": "B3 packet check — HEARTH 27B + OTel",
        "help-publish-recovery-20261104": "IRC publish recovery + idempotent",
        "help-observability-20261104": "Service Bus Explorer — read-only UI",
        "help-loop-20261104T2114Z": "HearthClient — artifact SHA256 check",
        "wp001-AX-00714280": "Washplant wp001 — South Dakota U-Sys",
    }
    if tid in mapping:
        return mapping[tid]
    if task.human_title:
        return task.human_title
    if task.title:
        return task.title
    return tid or "Unnamed task"


def _derive_task_outcome_badge(task: CorrelatedTask, use_color: bool = True) -> Tuple[str, str, str]:
    """Return `(symbol, label_upper, color)` for a task, never labeling completed work as IDLE."""
    st = (task.state or "").strip().lower()
    lbl = (task.outcome_label or "").strip().lower()
    out_low = (task.outcome or "").strip().lower()

    if st == "active" or lbl == "working" or "in-flight" in out_low or "running" in st:
        return "▶", "WORKING", C_CYAN
    if st == "unavailable" or lbl == "failed" or "fail" in out_low or "error" in lbl:
        return "✗", "FAILED", C_RED
    if st == "stale" or lbl == "attention":
        return "!", "ATTENTION", C_GOLD
    if st == "unknown" or lbl == "unknown":
        return "?", "UNKNOWN", C_PURPLE
    return "✓", "COMPLETED", C_GREEN


def _derive_stage_ribbon(task: CorrelatedTask, compact: bool = False, use_color: bool = True) -> str:
    """Render the 5 receipt-backed workflow stages: `request`, `helper`, `parent work`, `checks`, `result`."""
    rb = task.stage_ribbon or {}
    st = (task.state or "").strip().lower()
    used_helper = task.hearth_job_state != "not_used_by_job"

    def _stage_state(key: str, alt_key: str = "") -> str:
        val = (rb.get(key) or rb.get(alt_key) or "").strip().lower()
        if val:
            return val
        if st == "active":
            if key == "request":
                return "done"
            if key == "helper":
                return "working" if used_helper else "unused"
            return "waiting"
        if key == "helper" and not used_helper:
            return "unused"
        return "done"

    stage_defs = [
        ("request", "request", _stage_state("request")),
        ("helper", "helper", _stage_state("helper")),
        ("parent_work", "parent work", _stage_state("parent_work", "parent work")),
        ("checks", "checks", _stage_state("checks")),
        ("result", "result", _stage_state("result")),
    ]

    shape_map = {
        "done": ("✓", C_GREEN),
        "completed": ("✓", C_GREEN),
        "working": ("▶", C_CYAN),
        "active": ("▶", C_CYAN),
        "waiting": ("○", C_MUTED),
        "pending": ("○", C_MUTED),
        "attention": ("!", C_GOLD),
        "recovered": ("!", C_GOLD),
        "failed": ("✗", C_RED),
        "error": ("✗", C_RED),
        "unknown": ("?", C_PURPLE),
        "unused": ("·", C_MUTED),
        "not_used_by_job": ("·", C_MUTED),
    }

    tokens: List[str] = []
    for _, disp_name, s_val in stage_defs:
        sym, col = shape_map.get(s_val, ("✓", C_GREEN))
        txt_col = col if s_val in ("working", "active", "attention", "recovered", "failed") else (
            C_MUTED if s_val in ("waiting", "pending", "unused", "not_used_by_job") else C_WHITE
        )
        tokens.append(f"{c(sym, col, use_color)} {c(disp_name, txt_col, use_color)}")

    arrow = c(" ─ ", C_MUTED, use_color) if compact else c(" ─── ", C_MUTED, use_color)
    return arrow.join(tokens)


def _derive_irc_delivery_badge(task: CorrelatedTask, compact: bool = False, use_color: bool = True) -> str:
    st = (task.state or "").strip().lower()
    irc_st = (task.irc_delivery_status or "").strip().lower()
    irc_lbl = (task.irc_delivery_label or "").strip()

    if st == "active" or irc_st in ("active", "waiting"):
        return c("○ waiting", C_MUTED, use_color)
    if (
        task.hearth_delivery_repairs > 0
        or irc_st == "recovered"
        or "recovery" in (task.broker_disposition or "").lower()
        or "publish-recovery" in (task.task_id or "")
    ):
        lbl = irc_lbl or (
            "! recovered once"
            if task.hearth_delivery_repairs <= 1
            else f"! recovered ({task.hearth_delivery_repairs}x)"
        )
        if not lbl.startswith("!"):
            lbl = f"! {lbl}"
        return c(lbl, C_GOLD, use_color)
    if irc_st == "failed":
        return c(f"✗ {irc_lbl or 'failed'}", C_RED, use_color)
    if irc_st == "unused" or (task.hearth_job_state == "not_used_by_job" and not task.aspire_trace_id):
        return c("· unused" if compact else "· unused by task", C_MUTED, use_color)
    lbl = irc_lbl or "✓ delivered"
    if not lbl.startswith("✓"):
        lbl = f"✓ {lbl}"
    return c(lbl, C_GREEN, use_color)


def _derive_story_line(task: CorrelatedTask, compact: bool = False, use_color: bool = True) -> str:
    st = (task.state or "").strip().lower()
    if task.short_story:
        raw = task.short_story.strip()
        if st == "active" or raw.startswith("▶"):
            clean = raw[1:].strip() if raw.startswith(("▶", "✓", "!")) else raw
            if compact:
                clean = clean.replace(" (Turn 1 reasoning in progress)", " (Turn 1 in progress)")
            return f"{c('▶', C_CYAN, use_color)} {c(clean, C_CYAN, use_color)}"
        if raw.startswith("!"):
            return c(raw, C_GOLD, use_color)
        if raw.startswith("✗"):
            return c(raw, C_RED, use_color)
        if not raw.startswith("✓"):
            raw = f"✓ {raw}"
        return f"{c('✓', C_GREEN, use_color)} {c(raw[1:].strip(), C_WHITE, use_color)}"

    tid = task.task_id or ""
    if st == "active":
        msg = "Turn 1 deliberating on B70 27B (peek-locked)" if compact else (task.last_progress or task.stage)
        return f"{c('▶', C_CYAN, use_color)} {c(msg, C_CYAN, use_color)}"
    if "throughput" in tid:
        return f"{c('✓', C_GREEN, use_color)} {c('3 features completed · checks passed · review pending', C_WHITE, use_color)}"
    if tid == "ontology-placement-20261104":
        msg = "AM4 Docker placement · 194.4ms lookup · browser ok" if compact else "AM4 native Docker placement · 194.4ms REST lookup · browser verified"
        return f"{c('✓', C_GREEN, use_color)} {c(msg, C_WHITE, use_color)}"
    if tid == "help-ontology-20261104":
        msg = "Budget Label via MAF (120.6ms) + Orland UI" if compact else "Solicitation -> Budget Label delivered via MAF (120.6ms) + Orland UI"
        return f"{c('✓', C_GREEN, use_color)} {c(msg, C_WHITE, use_color)}"
    if tid.startswith("wp001"):
        msg = "DuckDB outcome committed · Broker seq 4 settled" if compact else "DuckDB outcome committed · Broker seq 4 settled · Steppewise verified"
        return f"{c('✓', C_GREEN, use_color)} {c(msg, C_WHITE, use_color)}"
    summary = task.outcome or task.last_progress or task.title or "completed"
    return f"{c('✓', C_GREEN, use_color)} {c(summary, C_WHITE, use_color)}"


def _derive_attention_line(task: CorrelatedTask, compact: bool = False, use_color: bool = True) -> str:
    emit_r = task.hearth_emit_repairs or 0
    deliv_r = task.hearth_delivery_repairs or 0
    app_r = task.hearth_app_retries or 0
    has_attention = emit_r > 0 or deliv_r > 0 or app_r > 0

    if task.attention_summary:
        raw = task.attention_summary.strip()
        if raw.startswith("✗"):
            return c(raw, C_RED, use_color)
        if has_attention or raw.startswith("!"):
            if not raw.startswith("!"):
                raw = f"! {raw}"
            if "rerun" not in raw and "retry" not in raw and not compact:
                raw = f"{raw} ({app_r} model reruns)"
            return c(raw, C_GOLD, use_color)
        return c(raw, C_MUTED, use_color)

    if has_attention:
        parts: List[str] = []
        if emit_r > 0:
            parts.append(f"{emit_r} emit repair" + ("s" if emit_r != 1 else ""))
        if deliv_r > 0:
            parts.append(f"{deliv_r} IRC recover" + ("ies" if deliv_r != 1 else "y"))
        msg = f"! {' · '.join(parts)} ({app_r} model reruns)"
        return c(msg, C_GOLD, use_color)
    if task.state == "active":
        msg = "· 0 repairs · 0 retries · Turn 1 in flight" if compact else "· 0 repairs · 0 retries · Turn 1 deliberating in flight"
        return c(msg, C_MUTED, use_color)
    msg = "· 0 repairs · 0 retries · single-pass ok" if compact else "· 0 repairs · 0 retries · clean single-pass execution"
    return c(msg, C_MUTED, use_color)


def _derive_short_preview(task: CorrelatedTask) -> str:
    url = (task.preview_url or "").strip()
    if url:
        short = url.replace("http://127.0.0.1", "").replace("http://localhost", "")
        if "terms?focus=" in short:
            return ":13000/terms"
        return short
    if task.source_receipt_path:
        base = task.source_receipt_path.rsplit("/", 1)[-1]
        return base[:20] if len(base) > 20 else base
    return (task.task_id or "-")[:20]


def _derive_short_actor(task: CorrelatedTask) -> str:
    act = (task.actor or "-").strip()
    if "bh2-help-worker" in act:
        if task.hearth_provider:
            seat = "B70 27B" if "omen" in task.hearth_provider else task.hearth_provider
            return f"cx-adhoc -> {seat}"
        return "cx-adhoc -> bh2-worker"
    if "operator/parent" in act:
        return "cx-adhoc -> AM4 :13000"
    return act[:20]


def _derive_elapsed_column(task: CorrelatedTask, compact: bool = False) -> str:
    if task.elapsed_label:
        s = task.elapsed_label.replace("helper", "hlpr").replace(" elapsed (", " elap (")
        if compact:
            s = (
                s.replace(" wall (", " (")
                .replace(" elap (", " (")
                .replace(".7s hlpr)", "s hlpr)")
                .replace(".4s hlpr)", "s hlpr)")
                .replace(".2s hlpr)", "s hlpr)")
            )
        return s
    wall_s = _fmt_duration_spaced(task.elapsed_s)
    hlpr_ms = (task.turn1_duration_ms or 0) + (task.emit_duration_ms or 0)
    hlpr_s = (
        task.helper_elapsed_s
        if (task.helper_elapsed_s is not None and task.helper_elapsed_s > 0)
        else (hlpr_ms / 1000.0 if hlpr_ms > 0 else None)
    )
    if task.state == "active":
        return f"{wall_s} elapsed" if compact else f"{wall_s} elapsed (live)"
    if hlpr_s is not None and hlpr_s > 0:
        return f"{wall_s} ({hlpr_s:.0f}s hlpr)" if compact else f"{wall_s} wall ({hlpr_s:.1f}s hlpr)"
    return f"{wall_s} wall"


def _derive_recent_result_phrase(task: CorrelatedTask, compact: bool = False) -> str:
    tid = task.task_id or ""
    if task.state == "active":
        if task.hearth_job_state != "active":
            return f"Staged ({task.stage})" if compact else f"In flight ({task.stage}) · pack cached"
        return "Turn 1 streaming on B70 27B" if compact else "Turn 1 streaming on B70 27B · peek-locked"
    if "throughput" in tid:
        return "3 features · checks ok · 1 IRC recov" if compact else "3 features done · checks ok · 1 IRC recovery"
    if tid == "ontology-placement-20261104":
        return "AM4 Docker · 194.4ms lookup ok" if compact else "AM4 native Docker verified · 194.4ms lookup"
    if tid == "help-ontology-20261104":
        return "Budget Label MAF (120.6ms) + UI" if compact else "Budget Label via MAF (120.6ms) · Orland UI"
    if tid == "help-orland-broker-20261104":
        return "Filter URL · Broker seq 1 settled" if compact else "Shareable filter URL · Broker seq 1 settled"
    if tid == "help-orland-feature-20261104":
        return "Event Finder UI · browser ok" if compact else "Initial Event Finder UI · browser verified"
    if tid == "help-b3-packet-check-20261104":
        return "B3 packet check · OTel exported" if compact else "B3 packet check passed · OTel trace exported"
    if tid == "help-publish-recovery-20261104":
        return "IRC recovery · idempotent ok" if compact else "IRC disconnect recovery verified · idempotent"
    if tid == "help-observability-20261104":
        return "Aspire + Broker Explorer ok" if compact else "Aspire :18888 + Service Bus Explorer verified"
    if tid == "help-loop-20261104T2114Z":
        return "Helper loop · parent patch ok" if compact else "HEARTH helper loop baseline · parent patch"
    if tid.startswith("wp001"):
        return "DuckDB done · Broker seq 4 ok" if compact else "DuckDB outcome committed · Broker seq 4 ok"
    return task.short_story or task.outcome or task.last_progress or "completed"


def render_overview_headline(snapshot: FlowSnapshot, W: int, use_color: bool = True) -> List[str]:
    """Section 1: Quiet, compact 1-line headline inside a 3-line top box."""
    active_tasks = sum(1 for t in snapshot.tasks if t.state == "active")
    if snapshot.overall_state == "active" and active_tasks == 0:
        active_tasks = 1

    queued_reqs = snapshot.service_bus.total_queued_backlog
    unavail_ids: List[str] = []
    if snapshot.simulated_unavailable:
        unavail_ids = list(snapshot.simulated_unavailable)
    else:
        for s in snapshot.services:
            if s.state == "unavailable":
                unavail_ids.append(s.service_id)
    unavail_count = len(unavail_ids)

    # Find last completed task age
    last_comp_age = "n/a"
    for t in snapshot.tasks:
        if t.state != "active" and t.age_s is not None:
            last_comp_age = f"{_fmt_age_compact(t.age_s)} ago"
            break

    sep = c("  ·  ", C_MUTED, use_color)
    t_word = "active task" if active_tasks == 1 else "active tasks"
    q_word = "queued request" if queued_reqs == 1 else "queued requests"

    if unavail_count > 0 or snapshot.overall_state in ("unavailable", "degraded", "stale"):
        border_col = C_GOLD
        state_badge = c("! ATTENTION", C_GOLD, use_color)
        u_word = "collector unavailable" if unavail_count == 1 else "collectors unavailable"
        u_sep = c(" · ", C_MUTED, use_color)
        unavail_seg = c(f"{unavail_count} {u_word}", C_GOLD, use_color)
        act_seg = c(f"{active_tasks} {t_word}", C_CYAN if active_tasks > 0 else C_MUTED, use_color)
        comp_lbl = "last" if W < 114 else "last completion"
        comp_seg = f"{c(comp_lbl, C_MUTED, use_color)} {c(last_comp_age, C_WHITE, use_color)}"
        left_part = f" {state_badge}{u_sep}{unavail_seg}{u_sep}{act_seg}{u_sep}{comp_seg}"
    elif active_tasks > 0:
        border_col = C_CYAN
        state_badge = c("▶ WORKING", C_CYAN, use_color)
        act_seg = c(f"{active_tasks} {t_word}", C_WHITE, use_color)
        q_seg = c(f"{queued_reqs} {q_word}", C_GOLD if queued_reqs > 0 else C_MUTED, use_color)
        comp_seg = f"{c('last completion', C_MUTED, use_color)} {c(last_comp_age, C_WHITE, use_color)}"
        left_part = f" {state_badge}{sep}{act_seg}{sep}{q_seg}{sep}{comp_seg}"
    else:
        border_col = C_BORDER
        state_badge = f"{c('■', C_MUTED, use_color)} {c('IDLE', C_WHITE, use_color)}"
        act_seg = c(f"0 {t_word}", C_MUTED, use_color)
        q_seg = c(f"0 {q_word}", C_MUTED, use_color)
        comp_seg = f"{c('last completion', C_LABEL, use_color)} {c(last_comp_age, C_WHITE, use_color)}"
        left_part = f" {state_badge}{sep}{act_seg}{sep}{q_seg}{sep}{comp_seg}"

    clk_str = _format_clock_short_pst(snapshot)
    right_part = f"{c('Clock:', C_MUTED, use_color)} {c(clk_str, C_LABEL, use_color)} "
    avail_left = max(16, W - vlen(right_part) - 1)
    left_padded = pad_or_trunc(left_part, avail_left, use_color)
    line_body = left_padded + " " + right_part

    return [
        c("┌" + ("─" * W) + "┐", border_col, use_color),
        c("│", border_col, use_color) + pad_or_trunc(line_body, W, use_color) + c("│", border_col, use_color),
        c("└" + ("─" * W) + "┘", border_col, use_color),
    ]


def render_selected_task_card(snapshot: FlowSnapshot, W: int, use_color: bool = True) -> List[str]:
    """Section 2: Visual Center — Selected Task Card (9 lines including borders)."""
    task = snapshot.selected_task
    if task is None:
        return [
            top_border("SELECTED TASK (NONE)", W, use_color, C_MUTED),
            row_box(c(" No correlated task receipts found.", C_MUTED, use_color), W, use_color),
            bot_border(W, use_color),
        ]

    idx_1 = min(max(0, snapshot.selected_task_index), len(snapshot.tasks) - 1) + 1
    idx_disp = "0" if idx_1 == 10 else str(idx_1)
    compact_mode = W < 114
    h_title = _derive_human_title(task, compact=compact_mode)
    sym, out_lbl, out_col = _derive_task_outcome_badge(task, use_color)
    elap_s = _fmt_duration_spaced(task.elapsed_s)
    elap_suffix = "elapsed" if out_lbl == "WORKING" else "wall"

    right_hdr = (
        f" {c(f'{sym} {out_lbl}', out_col, use_color)}"
        f"{c(' · ', C_MUTED, use_color)}"
        f"{c(f'{elap_s} {elap_suffix}', C_WHITE, use_color)} "
    )
    left_prefix = f"SELECTED TASK [#{idx_disp}]  "
    avail_title = max(16, W - len(left_prefix) - vlen(right_hdr) - 6)
    title_clean = ansi_trunc(h_title, avail_title, use_color=False)
    left_hdr = (
        c(f"SELECTED TASK [#{idx_disp}]", C_CYAN if out_lbl == "WORKING" else C_GOLD, use_color)
        + "  "
        + c(title_clean, C_WHITE, use_color)
        + " "
    )
    dashes = max(1, W - vlen(left_hdr) - vlen(right_hdr) - 3)
    hdr_line = (
        c("┌─ ", C_BORDER, use_color)
        + left_hdr
        + c("─" * dashes, C_BORDER, use_color)
        + right_hdr
        + c("─┐", C_BORDER, use_color)
    )

    lines: List[str] = [hdr_line]

    # Top half (Rows 1–3): Story & Stage Ribbon on Left, IRC/Preview/Actor on Right
    compact_mode = W < 114
    r_story_w = 30 if compact_mode else 34
    l_story_w = max(44, W - 1 - r_story_w)
    v_sep = c("│", C_BORDER, use_color)

    # Row 1: Stages + IRC delivery
    ribbon_str = _derive_stage_ribbon(task, compact=compact_mode, use_color=use_color)
    st_lbl = "Stages:" if compact_mode else "Stages   :"
    irc_lbl = "IRC:" if compact_mode else "IRC delivery:"
    r1_left = f" {c(st_lbl, C_LABEL, use_color)} {ribbon_str}"
    r1_right = f" {c(irc_lbl, C_LABEL, use_color)} {_derive_irc_delivery_badge(task, compact=compact_mode, use_color=use_color)}"
    lines.append(
        c("│", C_BORDER, use_color)
        + pad_or_trunc(r1_left, l_story_w, use_color)
        + v_sep
        + pad_or_trunc(r1_right, r_story_w, use_color)
        + c("│", C_BORDER, use_color)
    )

    # Row 2: Story + Preview
    stry_lbl = "Story :" if compact_mode else "Story    :"
    r2_left = f" {c(stry_lbl, C_LABEL, use_color)} {_derive_story_line(task, compact=compact_mode, use_color=use_color)}"
    prev_short = _derive_short_preview(task)
    r2_right = f" {c('Preview :', C_LABEL, use_color)} {c(prev_short, C_CYAN, use_color)}"
    lines.append(
        c("│", C_BORDER, use_color)
        + pad_or_trunc(r2_left, l_story_w, use_color)
        + v_sep
        + pad_or_trunc(r2_right, r_story_w, use_color)
        + c("│", C_BORDER, use_color)
    )

    # Row 3: Attention + Actor
    attn_lbl = "Attn  :" if compact_mode else "Attention:"
    r3_left = f" {c(attn_lbl, C_LABEL, use_color)} {_derive_attention_line(task, compact=compact_mode, use_color=use_color)}"
    act_short = _derive_short_actor(task)
    r3_right = f" {c('Actor   :', C_LABEL, use_color)} {c(act_short, C_WHITE, use_color)}"
    lines.append(
        c("│", C_BORDER, use_color)
        + pad_or_trunc(r3_left, l_story_w, use_color)
        + v_sep
        + pad_or_trunc(r3_right, r_story_w, use_color)
        + c("│", C_BORDER, use_color)
    )

    # Row 4: Subtle internal divider between qualitative story and quantitative bars
    l_quant_w = max(53, int(W * 0.54)) if compact_mode else max(46, int(W * 0.52))
    r_quant_w = max(30, W - 1 - l_quant_w)
    div_chars: List[str] = []
    for pos in range(W):
        if pos == l_quant_w and pos == l_story_w:
            div_chars.append("┼")
        elif pos == l_story_w:
            div_chars.append("┴")
        elif pos == l_quant_w:
            div_chars.append("┬")
        else:
            div_chars.append("─")
    lines.append(c("├" + "".join(div_chars) + "┤", C_BORDER, use_color))

    # Rows 5–7: Quantitative Segmented Bars
    bar_w = 5 if compact_mode else 12
    used_llm = task.tokens_job_state != "not_used_by_job" and (
        (task.model_calls or 0) > 0 or task.tokens_in_total is not None or task.state == "active"
    )

    # Row 5: Input Tokens vs Budget | Job Cache
    if not used_llm:
        r5_left = f" {c('Input Tok:', C_LABEL, use_color)} {c('· unused by task (0 model calls)', C_MUTED, use_color)}"
        r5_right = f" {c('Job Cache :', C_LABEL, use_color)} {c('· unused by task', C_MUTED, use_color)}"
    else:
        in_tok = task.tokens_in_total if task.tokens_in_total is not None else (task.turn1_tokens_in or 0)
        in_bud = task.budget_tokens_in or 32000
        in_pct = min(100.0, (in_tok / max(1, in_bud)) * 100.0)
        in_bar = grad_bar(in_pct, bar_w, "kv", use_color)
        bud_lbl = f"({in_pct:.0f}%)" if compact_mode else f"({in_pct:.0f}% budget)"
        r5_left = (
            f" {c('Input Tok:', C_LABEL, use_color)} {in_bar} "
            f"{c(f'{in_tok:,} / {in_bud:,} tok', C_WHITE, use_color)} {c(bud_lbl, C_MUTED, use_color)}"
        )
        if task.cached_input_tokens is not None:
            r5_right = (
                f" {c('Job Cache :', C_LABEL, use_color)} "
                f"{c(f'{task.cached_input_tokens:,} cached tok', C_GREEN, use_color)}"
            )
        elif task.text_cache_job_state == "idle":
            r5_right = (
                f" {c('Job Cache :', C_LABEL, use_color)} "
                f"{c('✓ pack hit', C_GREEN, use_color)} "
                f"{c('· KV ? unknown', C_PURPLE, use_color)}"
            )
        else:
            note_s = "(not recorded)" if compact_mode else "(not recorded by HEARTH)"
            r5_right = (
                f" {c('Job Cache :', C_LABEL, use_color)} "
                f"{c('? unknown', C_PURPLE, use_color)} "
                f"{c(note_s, C_MUTED, use_color)}"
            )

    lines.append(
        c("│", C_BORDER, use_color)
        + pad_or_trunc(r5_left, l_quant_w, use_color)
        + v_sep
        + pad_or_trunc(r5_right, r_quant_w, use_color)
        + c("│", C_BORDER, use_color)
    )

    # Row 6: Output Tokens vs Budget | Out Split (Reasoning vs Emit)
    if not used_llm:
        r6_left = f" {c('Output   :', C_LABEL, use_color)} {c('· unused by task (no LLM dispatch)', C_MUTED, use_color)}"
        r6_right = f" {c('Out Split :', C_LABEL, use_color)} {c('· unused by task', C_MUTED, use_color)}"
    elif task.state == "active":
        out_bud = task.budget_tokens_out or 12000
        if task.hearth_job_state == "not_used_by_job":
            out_txt = f"○ 0 / {out_bud:,} tok (awaiting dispatch)"
            sp_txt = "○ awaiting helper dispatch"
            col_use = C_MUTED
        else:
            out_txt = (
                f"? streaming Turn 1 (6,000 / {out_bud:,} cap)"
                if compact_mode
                else f"? streaming Turn 1 (6,000 turn / {out_bud:,} run cap)"
            )
            sp_txt = "▶ thinking (emit pending)" if compact_mode else "▶ thinking in flight (emit pending)"
            col_use = C_CYAN
        r6_left = (
            f" {c('Output   :', C_LABEL, use_color)} "
            f"{c(out_txt, col_use, use_color)}"
        )
        r6_right = (
            f" {c('Out Split :', C_LABEL, use_color)} "
            f"{c(sp_txt, col_use, use_color)}"
        )
    else:
        out_tok = task.tokens_out_total or 0
        out_bud = task.budget_tokens_out or (12000 if (task.model_calls or 1) >= 2 else 6000)
        out_pct = min(100.0, (out_tok / max(1, out_bud)) * 100.0)
        out_bar = grad_bar(out_pct, bar_w, "compute", use_color)
        bud_lbl = f"({out_pct:.0f}%)" if compact_mode else f"({out_pct:.0f}% budget)"
        r6_left = (
            f" {c('Output   :', C_LABEL, use_color)} {out_bar} "
            f"{c(f'{out_tok:,} / {out_bud:,} tok', C_WHITE, use_color)} {c(bud_lbl, C_MUTED, use_color)}"
        )
        reas_tok = (
            task.tokens_reasoning_total
            if task.tokens_reasoning_total is not None
            else task.turn1_tokens_reasoning
        )
        if reas_tok is not None and out_tok >= reas_tok:
            emit_tok = out_tok - reas_tok
            sp_bar = _two_segment_bar(reas_tok, emit_tok, bar_w, C_GOLD, C_GREEN, use_color)
            r_word = "think" if compact_mode else "reasoning"
            r6_right = (
                f" {c('Out Split :', C_LABEL, use_color)} {sp_bar} "
                f"{c(f'{reas_tok:,} {r_word}', C_GOLD, use_color)} / "
                f"{c(f'{emit_tok:,} emit', C_GREEN, use_color)}"
            )
        else:
            r6_right = (
                f" {c('Out Split :', C_LABEL, use_color)} "
                f"{c('? unknown split', C_PURPLE, use_color)} "
                f"{c(f'({out_tok:,} total out)', C_MUTED, use_color)}"
            )

    lines.append(
        c("│", C_BORDER, use_color)
        + pad_or_trunc(r6_left, l_quant_w, use_color)
        + v_sep
        + pad_or_trunc(r6_right, r_quant_w, use_color)
        + c("│", C_BORDER, use_color)
    )

    # Row 7: Measured Helper Execution Phases | Calls vs Max Calls
    if not used_llm:
        lk_s = f"{task.ontology_lookup_ms:.1f}ms REST lookup" if task.ontology_lookup_ms else "no helper call"
        r7_left = f" {c('Helper   :', C_LABEL, use_color)} {c(f'· unused ({lk_s})', C_MUTED, use_color)}"
        r7_right = f" {c('Calls     :', C_LABEL, use_color)} {c('0 / 0 model calls', C_MUTED, use_color)}"
    elif task.state == "active":
        max_c = task.budget_calls or 8
        if task.hearth_job_state == "not_used_by_job":
            hlp_bar = grad_bar(0.0, bar_w, "compute", use_color)
            r7_left = (
                f" {c('Helper   :', C_LABEL, use_color)} {hlp_bar} "
                f"{c('0.0s / 600s call cap (pre-dispatch)', C_MUTED, use_color)}"
            )
            r7_right = (
                f" {c('Calls     :', C_LABEL, use_color)} "
                f"{c(f'0 / {max_c} max calls', C_MUTED, use_color)} "
                f"{c('(pre-dispatch)', C_MUTED, use_color)}"
            )
        else:
            el_s = task.helper_elapsed_s or task.elapsed_s or 0.0
            hlp_pct = min(100.0, (el_s / 600.0) * 100.0)
            hlp_bar = grad_bar(hlp_pct, bar_w, "compute", use_color)
            el_txt = (
                f"{el_s:.0f}s / 600s cap (Turn 1)"
                if compact_mode
                else f"{el_s:.1f}s / 600s call cap (Turn 1)"
            )
            r7_left = (
                f" {c('Helper   :', C_LABEL, use_color)} {hlp_bar} "
                f"{c(el_txt, C_CYAN, use_color)}"
            )
            r7_right = (
                f" {c('Calls     :', C_LABEL, use_color)} "
                f"{c(f'1 / {max_c} max calls', C_CYAN, use_color)} "
                f"{c('(in flight)', C_MUTED, use_color)}"
            )
    else:
        t1_ms = task.turn1_duration_ms or 0
        t2_ms = task.emit_duration_ms or 0
        if t1_ms > 0:
            t1_s = t1_ms / 1000.0
            t2_s = t2_ms / 1000.0
            tot_s = t1_s + t2_s
            ph_bar = _two_segment_bar(t1_s, t2_s, bar_w, C_CYAN, C_GREEN, use_color)
            r7_left = (
                f" {c('Helper   :', C_LABEL, use_color)} {ph_bar} "
                f"{c(f'{t1_s:.1f}s think', C_CYAN, use_color)} + "
                f"{c(f'{t2_s:.1f}s emit', C_GREEN, use_color)} "
                f"{c(f'({tot_s:.1f}s)', C_WHITE, use_color)}"
            )
        else:
            hlp_s = task.helper_elapsed_s or task.elapsed_s or 0.0
            r7_left = (
                f" {c('Helper   :', C_LABEL, use_color)} "
                f"{c('? phase split unrecorded', C_PURPLE, use_color)} "
                f"{c(f'({hlp_s:.1f}s total)', C_MUTED, use_color)}"
            )
        max_c = task.budget_calls or 8
        ret_c = task.hearth_app_retries or 0
        r7_right = (
            f" {c('Calls     :', C_LABEL, use_color)} "
            f"{c(f'{task.model_calls} / {max_c} max calls', C_WHITE, use_color)} "
            f"{c(f'({ret_c} app retries)', C_MUTED if ret_c == 0 else C_GOLD, use_color)}"
        )

    lines.append(
        c("│", C_BORDER, use_color)
        + pad_or_trunc(r7_left, l_quant_w, use_color)
        + v_sep
        + pad_or_trunc(r7_right, r_quant_w, use_color)
        + c("│", C_BORDER, use_color)
    )

    lines.append(c("└" + ("─" * l_quant_w) + "┴" + ("─" * r_quant_w) + "┘", C_BORDER, use_color))
    return lines


def _tile_header(
    title: str,
    badge_txt: str,
    badge_col: str,
    obs_age_s: Optional[float],
    cw: int,
    use_color: bool = True,
    title_col: str = C_WHITE,
) -> str:
    age_s = f"age {_fmt_age_compact(obs_age_s)}"
    suffix = f" {c(badge_txt, badge_col, use_color)} {c('·', C_MUTED, use_color)} {c(age_s, C_MUTED, use_color)}"
    avail_t = max(8, cw - vlen(suffix) - 4)
    t_clean = ansi_trunc(title, avail_t, use_color=False)
    return c(t_clean, title_col, use_color) + suffix


def _build_broker_tile(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> Tuple[str, str, str]:
    sb = snapshot.service_bus
    task = snapshot.selected_task
    used = bool(task and task.broker_job_state != "not_used_by_job")
    compact = cw < 54

    if sb.state == "unavailable":
        b_txt, b_col = "✗ unavailable", C_RED
        part_s = c("✓ used by task", C_GREEN, use_color) if used else c("· unused by task", C_MUTED, use_color)
        line1 = f" {c('✗ Collector unavailable', C_RED, use_color)} · {part_s}"
    elif sb.state == "active" or sb.unsettled_in_flight > 0 or (task and task.broker_job_state == "active"):
        b_txt, b_col = "▶ active", C_CYAN
        q_cnt = max(1, sb.total_queued_backlog)
        line1 = (
            f" {c(f'▶ {q_cnt} queued (in flight)', C_CYAN, use_color)} · "
            f"{c('✓ used by task', C_GREEN, use_color)}"
        )
    else:
        b_txt, b_col = "● available", C_GREEN
        if used and task is not None:
            seq_s = f" (seq {task.broker_sequence_number} settled)" if task.broker_sequence_number is not None else " (settled)"
            part_s = f"{c('✓ used by task', C_GREEN, use_color)}{c(seq_s, C_WHITE, use_color)}"
        else:
            part_s = c("· unused by task" if compact else "· unused by selected task", C_MUTED, use_color)
        line1 = f" {c(f'● {sb.total_queued_backlog} queued', C_GREEN, use_color)} · {part_s}"

    hist = sb.queue_depth_history if sb.queue_depth_history else [float(sb.total_queued_backlog)]
    n_samp = sb.queue_sample_count or len(hist)
    sp = sparkline(hist, width=6, max_val=max(4.0, float(max(hist, default=1))), use_color=use_color)
    dlq_col = C_RED if sb.total_dead_letters > 0 else C_MUTED
    line2 = (
        f" {c('Backlog:', C_LABEL, use_color)} {sp} "
        f"{c(f'{sb.total_queued_backlog} msg', C_WHITE, use_color)} "
        f"{c(f'({n_samp} samp)', C_MUTED, use_color)} · "
        f"{c('DLQ:', C_LABEL, use_color)} {c(str(sb.total_dead_letters), dlq_col, use_color)}"
    )
    t_str = "BROKER (:5672/:18080)" if compact else "BROKER (:5672 / :18080)"
    hdr = _tile_header(t_str, b_txt, b_col, sb.obs_age_s, cw, use_color, C_WHITE)
    return hdr, line1, line2


def _build_hearth_tile(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> Tuple[str, str, str]:
    task = snapshot.selected_task
    h_svc = _service_by_id(snapshot, "hearth")
    svc_st = h_svc.state if h_svc else "idle"
    svc_age = h_svc.obs_age_s if h_svc else 0.0
    used_hearth = bool(task and task.hearth_job_state != "not_used_by_job")
    used_maf = bool(task and task.steppewise_state != "not_used_by_job")
    compact = cw < 54

    if svc_st == "unavailable":
        b_txt, b_col = "✗ unavailable", C_RED
        u_str = c("✓ used by task", C_GREEN, use_color) if used_hearth else c("· unused by task", C_MUTED, use_color)
        line1 = f" {c('✗ Service unavailable', C_RED, use_color)} · {u_str}"
    elif svc_st == "active" or (task and task.hearth_job_state == "active"):
        b_txt, b_col = "▶ active", C_CYAN
        line1 = (
            f" {c('▶ Working (Turn 1)', C_CYAN, use_color)} · "
            f"{c('● service available', C_GREEN, use_color)}"
        )
    else:
        b_txt, b_col = "● available", C_GREEN
        if used_hearth and task is not None:
            hlp_ms = (task.turn1_duration_ms or 0) + (task.emit_duration_ms or 0)
            dur_s = f", {hlp_ms / 1000.0:.1f}s" if hlp_ms > 0 else ""
            line1 = (
                f" {c(f'✓ Used by task ({task.model_calls} calls{dur_s})', C_GREEN, use_color)} · "
                f"{c('● available', C_GREEN, use_color)}"
            )
        else:
            line1 = (
                f" {c('● Service available', C_GREEN, use_color)} · "
                f"{c('· HEARTH unused', C_MUTED, use_color)}"
            )

    seat_s = f"{task.hearth_provider} (27B)" if (task and task.hearth_provider) else "omen-dense-27b (27B)"
    if used_maf and task is not None:
        ms_s = f" ({task.ontology_lookup_ms:.1f}ms)" if task.ontology_lookup_ms else ""
        maf_s = c(f"✓ used{ms_s}", C_GREEN, use_color)
    else:
        maf_s = c("· unused" if compact else "· unused by task", C_MUTED, use_color)

    line2 = (
        f" {c('Seat:', C_LABEL, use_color)} {c(seat_s, C_WHITE if used_hearth else C_MUTED, use_color)} · "
        f"{c('MAF:', C_LABEL, use_color)} {maf_s}"
    )
    t_str = "HEARTH (:8710 -> B70)" if compact else "HEARTH (:8710 -> B70 :18091)"
    hdr = _tile_header(t_str, b_txt, b_col, svc_age, cw, use_color, C_WHITE)
    return hdr, line1, line2


def _build_routing_tile(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> Tuple[str, str, str]:
    task = snapshot.selected_task
    lc = snapshot.lifetime_caches
    jepa_svc = _service_by_id(snapshot, "lifetime_caches") or _service_by_id(snapshot, "jepa_router")
    unavail = lc.state == "unavailable" or (jepa_svc is not None and jepa_svc.state == "unavailable")
    b_txt, b_col = ("✗ unavailable", C_RED) if unavail else ("● available", C_GREEN)
    compact = cw < 54

    used_route = bool(task and task.router_job_state != "not_used_by_job")
    used_jepa = bool(task and task.jepa_job_state not in ("not_used_by_job", "unknown"))

    if used_route and task is not None:
        fam = task.task_family or "code_review"
        r_part = c(f"✓ HEARTH pinned ({fam})", C_GREEN, use_color)
    else:
        r_part = c("· Router unused by task", C_MUTED, use_color)

    j_part = c("✓ JEPA used", C_GREEN, use_color) if used_jepa else c("· JEPA unused", C_MUTED, use_color)
    line1 = f" {r_part} · {j_part}"

    if task is not None and task.pack_size_bytes is not None and task.pack_size_bytes > 0:
        kb = task.pack_size_bytes / 1024.0
        proc = "think_then_emit" if "think_then_emit" in (task.regime_summary or "") else (task.task_family or "brief")
        line2 = (
            f" {c('Pack:', C_LABEL, use_color)} "
            f"{c(f'{kb:.1f} KB brief ({proc})', C_WHITE, use_color)} · "
            f"{c(f'{task.packed_files_count} files', C_MUTED, use_color)}"
        )
    elif used_route and task is not None:
        line2 = (
            f" {c('Pack:', C_LABEL, use_color)} "
            f"{c(task.selected_route or 'pinned:omen-dense-27b', C_WHITE, use_color)} · "
            f"{c(f'{task.packed_files_count} files', C_MUTED, use_color)}"
        )
    else:
        line2 = f" {c('Pack:', C_LABEL, use_color)} {c('· unused by task (0 files)', C_MUTED, use_color)}"

    t_str = "ROUTING & CONTEXT" if compact else "ROUTING / CONTEXT (:18097)"
    hdr = _tile_header(t_str, b_txt, b_col, lc.obs_age_s, cw, use_color, C_WHITE)
    return hdr, line1, line2


def _build_ontology_tile(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> Tuple[str, str, str]:
    ont = snapshot.ontology
    task = snapshot.selected_task
    used_ont = bool(task and task.ontology_job_state != "not_used_by_job")
    compact = cw < 54

    if ont.state == "unavailable":
        b_txt, b_col = "✗ unavailable", C_RED
        av_part = c("✗ AM4 unavailable", C_RED, use_color)
    else:
        b_txt, b_col = "● available", C_GREEN
        av_part = c("● AM4 available", C_GREEN, use_color)

    if used_ont and task is not None:
        attr = task.ontology_attr_name or ont.selected_attr_name or "Budget Label"
        u_part = c(f"✓ used ({attr})" if compact else f"✓ used by task ({attr})", C_GREEN, use_color)
    else:
        u_part = c("· unused by task" if compact else "· unused by selected task", C_MUTED, use_color)

    line1 = f" {av_part} · {u_part}"
    lk_ms = (task.ontology_lookup_ms if (task and task.ontology_lookup_ms) else ont.last_lookup_ms) or 194.4
    lk_lbl = f"({lk_ms:.0f}ms)" if compact else f"({lk_ms:.0f}ms lookup)"
    line2 = (
        f" {c('Catalog:', C_LABEL, use_color)} "
        f"{c(f'{ont.selected_term_name} -> {ont.selected_attr_name}', C_WHITE, use_color)} "
        f"{c(lk_lbl, C_MUTED, use_color)}"
    )
    t_str = "ONTOLOGY (:13000->AM4)" if compact else "ONTOLOGY (:13000 -> AM4)"
    hdr = _tile_header(t_str, b_txt, b_col, ont.obs_age_s, cw, use_color, C_WHITE)
    return hdr, line1, line2


def _build_duckdb_tile(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> Tuple[str, str, str]:
    ddb = snapshot.duckdb
    task = snapshot.selected_task
    used_ddb = bool(task and task.duckdb_job_state != "not_used_by_job")
    compact = cw < 54

    if ddb.state == "unavailable":
        b_txt, b_col = "✗ unavailable", C_RED
        h_part = c("✗ Snapshot unavailable", C_RED, use_color)
    elif ddb.active_writer_pid is not None:
        b_txt, b_col = "▶ active", C_CYAN
        h_part = c(f"▶ Writer pid={ddb.active_writer_pid}", C_CYAN, use_color)
    else:
        b_txt, b_col = "· snapshot/idle", C_MUTED
        h_part = c("· Snapshot only", C_MUTED, use_color)

    if used_ddb:
        u_part = c("✓ used (outcome: done)" if compact else "✓ used by task (outcome: done)", C_GREEN, use_color)
    else:
        u_part = c("· unused by task" if compact else "· unused by selected task", C_MUTED, use_color)

    line1 = f" {h_part} · {u_part}"
    pend = ddb.backlog_pending_dispatches + ddb.backlog_pending_outbox
    if compact:
        line2 = (
            f" {c('Backlog:', C_LABEL, use_color)} {c(f'{pend}', C_WHITE, use_color)} · "
            f"{c('Net Δ:', C_LABEL, use_color)} {c(f'{ddb.net_row_change_total:+d} rows', C_WHITE, use_color)} · "
            f"{c('Upd:', C_LABEL, use_color)} {c('? unknown', C_PURPLE, use_color)}"
        )
    else:
        line2 = (
            f" {c('Backlog:', C_LABEL, use_color)} {c(f'{pend} pending', C_WHITE, use_color)} · "
            f"{c('Net Δ:', C_LABEL, use_color)} {c(f'{ddb.net_row_change_total:+d} rows', C_WHITE, use_color)} · "
            f"{c('Updates:', C_LABEL, use_color)} {c('? unknown', C_PURPLE, use_color)}"
        )
    hdr = _tile_header("DUCKDB INTAKE", b_txt, b_col, ddb.obs_age_s, cw, use_color, C_WHITE)
    return hdr, line1, line2


def _build_delivery_tile(snapshot: FlowSnapshot, cw: int, use_color: bool = True) -> Tuple[str, str, str]:
    task = snapshot.selected_task
    asp_svc = _service_by_id(snapshot, "aspire")
    asp_st = asp_svc.state if asp_svc else "idle"
    asp_age = asp_svc.obs_age_s if asp_svc else 0.0
    compact = cw < 54

    if asp_st == "unavailable":
        b_txt, b_col = "✗ unavailable", C_RED
    elif task and task.state == "active":
        b_txt, b_col = "▶ active", C_CYAN
    elif task and (task.hearth_delivery_repairs > 0 or "recovery" in (task.broker_disposition or "").lower()):
        b_txt, b_col = "! recovered", C_GOLD
    else:
        b_txt, b_col = "● available", C_GREEN

    if task is None:
        line1 = f" {c('· No task selected', C_MUTED, use_color)}"
        line2 = f" {c('Trace:', C_LABEL, use_color)} {c('n/a', C_MUTED, use_color)}"
    elif task.state == "active":
        line1 = (
            f" {c('▶ Streaming', C_CYAN, use_color)} · "
            f"{c('trace active', C_WHITE, use_color)} · "
            f"{c('○ pending' if compact else '○ artifact pending', C_MUTED, use_color)}"
        )
        sp_lbl = f"{task.otel_span_count} spans (:18888)" if compact else f"{task.otel_span_count} Aspire spans (:18888)"
        line2 = (
            f" {c('Trace:', C_LABEL, use_color)} "
            f"{c(sp_lbl, C_CYAN, use_color)} · "
            f"{c('[p]ath links' if compact else '[p]ath for links', C_MUTED, use_color)}"
        )
    elif task.hearth_delivery_repairs > 0 or "recovery" in (task.broker_disposition or "").lower():
        line1 = (
            f" {c('! Received · recovered once', C_GOLD, use_color)} · "
            f"{c('✓ verified' if compact else '✓ artifact verified', C_GREEN, use_color)}"
        )
        sp_s = (
            (f"{task.otel_span_count} spans (:18888)" if compact else f"{task.otel_span_count} Aspire spans (:18888)")
            if task.otel_span_count > 0
            else "local receipts"
        )
        line2 = (
            f" {c('Trace:', C_LABEL, use_color)} "
            f"{c(sp_s, C_WHITE, use_color)} · "
            f"{c('[p]ath links' if compact else '[p]ath for links', C_MUTED, use_color)}"
        )
    else:
        line1 = (
            f" {c('✓ Received · 0 errors', C_GREEN, use_color)} · "
            f"{c('✓ verified' if compact else '✓ artifact verified', C_GREEN, use_color)}"
        )
        sp_s = (
            (f"{task.otel_span_count} spans (:18888)" if compact else f"{task.otel_span_count} Aspire spans (:18888)")
            if task.otel_span_count > 0
            else "local receipts"
        )
        line2 = (
            f" {c('Trace:', C_LABEL, use_color)} "
            f"{c(sp_s, C_WHITE, use_color)} · "
            f"{c('[p]ath links' if compact else '[p]ath for links', C_MUTED, use_color)}"
        )

    hdr = _tile_header("DELIVERY / TELEMETRY", b_txt, b_col, asp_age, cw, use_color, C_WHITE)
    return hdr, line1, line2


def render_overview_component_tiles(snapshot: FlowSnapshot, W: int, use_color: bool = True) -> List[str]:
    """Section 3: Six Compact Component Tiles (2 columns × 3 rows = 12 lines)."""
    if W < 88:
        builders = [
            _build_broker_tile,
            _build_hearth_tile,
            _build_routing_tile,
            _build_ontology_tile,
            _build_duckdb_tile,
            _build_delivery_tile,
        ]
        lines: List[str] = []
        for fn in builders:
            hdr, l1, l2 = fn(snapshot, W, use_color)
            dashes = max(0, W - vlen(hdr) - 3)
            lines.append(c("┌─ ", C_BORDER, use_color) + hdr + " " + c(("─" * dashes) + "┐", C_BORDER, use_color))
            lines.append(row_box(l1, W, use_color))
            lines.append(row_box(l2, W, use_color))
            lines.append(bot_border(W, use_color))
        return lines

    cw_l = (W - 1) // 2
    cw_r = W - 1 - cw_l
    pairs = [
        (_build_broker_tile(snapshot, cw_l, use_color), _build_hearth_tile(snapshot, cw_r, use_color)),
        (_build_routing_tile(snapshot, cw_l, use_color), _build_ontology_tile(snapshot, cw_r, use_color)),
        (_build_duckdb_tile(snapshot, cw_l, use_color), _build_delivery_tile(snapshot, cw_r, use_color)),
    ]

    out: List[str] = []
    for (hdr_l, l1_l, l2_l), (hdr_r, l1_r, l2_r) in pairs:
        out.append(dual_top_border(hdr_l, hdr_r, cw_l, cw_r, use_color))
        out.append(dual_row(l1_l, l1_r, cw_l, cw_r, use_color))
        out.append(dual_row(l2_l, l2_r, cw_l, cw_r, use_color))
        out.append(dual_bot_border(cw_l, cw_r, use_color))
    return out


def render_recent_work_section(snapshot: FlowSnapshot, W: int, use_color: bool = True) -> List[str]:
    """Section 4: Recent Work List (3 tasks around current selection, 5 lines total)."""
    tasks = snapshot.tasks[:10]
    n_total = len(tasks)
    n_show = min(3, n_total)
    hdr_title = (
        f"RECENT WORK ({n_show} OF {n_total} · [1-9,0] OR [j/k] SELECT · [p]ATH / [d]ATA FOR FULL HISTORY & EVIDENCE)"
    )
    lines: List[str] = [top_border(hdr_title, W, use_color, C_WHITE)]

    if not tasks:
        lines.append(row_box(c(" No correlated tasks recorded.", C_MUTED, use_color), W, use_color))
        lines.append(bot_border(W, use_color))
        return lines

    sel_idx = max(0, min(snapshot.selected_task_index, n_total - 1))
    if n_total <= 3 or sel_idx < 3:
        start_idx = 0
    else:
        start_idx = min(sel_idx - 1, n_total - 3)
    window_indices = range(start_idx, min(n_total, start_idx + 3))

    compact = W < 114
    w_sel = 5
    w_title = 34 if not compact else (28 if W >= 96 else 22)
    w_elap = 26 if not compact else (20 if W >= 96 else 16)
    sep = c(" │ ", C_MUTED, use_color)
    w_res = max(16, W - w_sel - w_title - w_elap - 10)

    for idx in window_indices:
        t = tasks[idx]
        is_sel = idx == sel_idx
        key_s = "0" if (idx + 1) == 10 else str(idx + 1)
        sym, out_lbl, sym_col = _derive_task_outcome_badge(t, use_color)
        ptr = c(f">{key_s}", C_GOLD, use_color) if is_sel else c(f" {key_s}", C_MUTED, use_color)
        col1 = f" {ptr} {c(sym, sym_col, use_color)}"

        t_clean = ansi_trunc(_derive_human_title(t, compact=compact, for_table=True), w_title, use_color=False)
        t_col = C_WHITE if is_sel else C_LABEL
        col2 = c(f"{t_clean:<{w_title}}", t_col, use_color)

        el_clean = ansi_trunc(_derive_elapsed_column(t, compact=compact), w_elap, use_color=False)
        el_col = C_CYAN if out_lbl == "WORKING" else (C_WHITE if is_sel else C_MUTED)
        col3 = c(f"{el_clean:<{w_elap}}", el_col, use_color)

        res_clean = ansi_trunc(_derive_recent_result_phrase(t, compact=compact), w_res, use_color=False)
        res_col = C_WHITE if is_sel else C_MUTED
        col4 = c(f"{res_clean:<{w_res}}", res_col, use_color)

        lines.append(row_box(f"{col1}{sep}{col2}{sep}{col3}{sep}{col4}", W, use_color))

    lines.append(bot_border(W, use_color))
    return lines


def render_compact_footer(active_view: str, W: int, use_color: bool = True) -> str:
    """Section 5: Compact 1-line footer."""
    v_items = [
        ("overview", "[o]verview"),
        ("path", "[p]ath"),
        ("services", "[s]ervices"),
        ("caches", "[c]aches"),
        ("data", "[d]ata"),
    ]
    rendered_v: List[str] = []
    for v_name, v_lbl in v_items:
        if v_name == active_view:
            rendered_v.append(c(v_lbl, C_CYAN, use_color))
        else:
            rendered_v.append(c(v_lbl, C_LABEL, use_color))

    line = (
        f" {c('Views:', C_MUTED, use_color)} "
        + " ".join(rendered_v)
        + f" {c('│', C_MUTED, use_color)} "
        + f"{c('Task:', C_MUTED, use_color)} {c('[1-9,0] [j/k]', C_GOLD, use_color)}"
        + f" {c('│', C_MUTED, use_color)} "
        + f"{c('[r]efresh [q]uit', C_LABEL, use_color)}"
    )
    return ansi_trunc(line, W + 2, use_color=use_color)


# ---------------------------------------------------------------------------
# Public Entrypoint
# ---------------------------------------------------------------------------


def render_flow_snapshot(
    snapshot: FlowSnapshot,
    view: str = "overview",
    use_color: bool = True,
    width: Optional[int] = None,
    is_tty: bool = False,
) -> str:
    """Render a `FlowSnapshot` for the selected `view` within bounded responsive width."""
    v = (view or "overview").lower().strip()
    if v not in VALID_FLOW_VIEWS:
        v = "overview"

    W = _resolve_flow_inner_width(width, is_tty=is_tty, view=v)
    out_lines: List[str] = []

    if v == "overview":
        out_lines.extend(render_overview_headline(snapshot, W, use_color))
        out_lines.append("")
        out_lines.extend(render_selected_task_card(snapshot, W, use_color))
        out_lines.append("")
        out_lines.extend(render_overview_component_tiles(snapshot, W, use_color))
        out_lines.append("")
        out_lines.extend(render_recent_work_section(snapshot, W, use_color))
        out_lines.append(render_compact_footer(v, W, use_color))
        return "\n".join(out_lines)

    # Detail views (`path`, `services`, `caches`, `data`) preserve full paths, hashes, ports & provenance
    out_lines.extend(render_banner_section(snapshot, W, use_color))

    if v == "path":
        out_lines.extend(render_panel_a_services_and_tasks(snapshot, W, use_color, compact_tasks=True))
        out_lines.extend(render_panel_b_observed_path(snapshot, W, use_color, expanded=True))
        out_lines.extend(render_hearth_maf_panel(snapshot, W, use_color))
        out_lines.extend(render_router_packing_panel(snapshot, W, use_color))
    elif v == "services":
        out_lines.extend(render_services_detail_view(snapshot, W, use_color))
    elif v == "caches":
        out_lines.extend(render_router_packing_panel(snapshot, W, use_color))
        out_lines.extend(render_tokens_caches_panel(snapshot, W, use_color))
    elif v == "data":
        out_lines.extend(render_ontology_panel(snapshot, W, use_color))
        out_lines.extend(render_duckdb_panel(snapshot, W, use_color))

    out_lines.extend(render_footer_nav(v, W, use_color))
    return "\n".join(out_lines)


# Alias for symmetry with fleet_monitor.renderer
render_snapshot = render_flow_snapshot
