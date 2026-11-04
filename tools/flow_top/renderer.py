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
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

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
    mid_border,
    pad_or_trunc,
    resolve_inner_width,
    row_box,
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
    lines.append(
        _panel_header(
            "EXPANDED SERVICE AVAILABILITY TABLE (ALL 8 MONITORED SERVICES)",
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

    W = resolve_inner_width(width, is_tty=is_tty)
    out_lines: List[str] = []

    # 1. Top Double-Border Banner is always visible across all views
    out_lines.extend(render_banner_section(snapshot, W, use_color))

    if v == "overview":
        out_lines.extend(render_panel_a_services_and_tasks(snapshot, W, use_color))
        out_lines.extend(render_panel_b_observed_path(snapshot, W, use_color, expanded=False))
        out_lines.extend(render_component_columns(snapshot, W, use_color))
    elif v == "path":
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
